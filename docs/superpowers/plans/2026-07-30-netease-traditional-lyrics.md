# NetEase Traditional Chinese Lyrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert NetEase Chinese original lyrics and Chinese translation layers to Traditional Chinese while preserving Japanese, Korean, and English originals.

**Architecture:** Extract NetEase’s LRC-to-`LyricsData` conversion into a pure module. Parse the whole original lyric first, use the shared corpus-language detector to decide whether original lines should pass through OpenCC, and keep all UI surfaces consistent by returning already-converted line, word, translation, and plain-text data.

**Tech Stack:** TypeScript, `opencc-js`, Node test runner, Chrome extension service worker

---

## File Structure

- Create `everyric2-chrome/src/lib/netease-lyrics.ts`: language-aware NetEase lyric conversion.
- Create `everyric2-chrome/tests/netease-lyrics.test.mjs`: Chinese original, timed-word, Japanese original, and translation regression tests.
- Modify `everyric2-chrome/src/background.ts`: import the shared converter and remove the inline duplicate.
- Modify `everyric2-chrome/src/lib/netease.ts`: correct the stale documentation about display conversion.
- Modify `everyric2-chrome/tests/netease-priority.test.mjs`: assert service-worker wiring remains centralized.

### Task 1: Build and Test the Language-Aware Converter

**Files:**
- Create: `everyric2-chrome/src/lib/netease-lyrics.ts`
- Create: `everyric2-chrome/tests/netease-lyrics.test.mjs`

- [ ] **Step 1: Write failing conversion tests**

Create `everyric2-chrome/tests/netease-lyrics.test.mjs`:

```js
import assert from 'node:assert/strict';
import test from 'node:test';

import { neteaseToLyricsData } from '../src/lib/netease-lyrics.ts';

test('Chinese NetEase originals become Traditional Chinese everywhere', () => {
  const data = neteaseToLyricsData({
    lrc: [
      '[00:01.00]静止了这世界像张照片',
      '[00:02.00]点不着的香烟',
    ].join('\n'),
    tlyric: null,
  }, 'zh');

  assert.deepEqual(data?.lines.map(line => line.text), [
    '靜止了這世界像張照片',
    '點不著的香菸',
  ]);
  assert.equal(
    data?.plainText,
    '靜止了這世界像張照片\n點不著的香菸',
  );
});

test('Chinese timed words and their composed line use the same Traditional text', () => {
  const data = neteaseToLyricsData({
    lrc: [
      '[00:01.00]',
      '<00:01.00>点',
      '<00:01.20>不着',
      '<00:01.40>的',
      '<00:01.60>香烟',
    ].join(''),
    tlyric: null,
  }, 'zh');

  assert.equal(data?.lines[0]?.text, '點 不著 的 香菸');
  assert.deepEqual(
    data?.lines[0]?.words?.map(word => word.word),
    ['點', '不著', '的', '香菸'],
  );
  assert.equal(data?.plainText, '點 不著 的 香菸');
});

test('Japanese originals keep Japanese kanji while Chinese translations become Traditional', () => {
  const data = neteaseToLyricsData({
    lrc: [
      '[00:01.00]叶えたい未来がある',
      '[00:02.00]君の声',
    ].join('\n'),
    tlyric: [
      '[00:01.00]想要实现的未来',
      '[00:02.00]你的声音',
    ].join('\n'),
  }, 'zh');

  assert.deepEqual(data?.lines.map(line => line.text), [
    '叶えたい未来がある',
    '君の声',
  ]);
  assert.deepEqual(data?.lines.map(line => line.translation), [
    '想要實現的未來',
    '你的聲音',
  ]);
  assert.deepEqual(data?.translationsByLang?.zh, [
    '想要實現的未來',
    '你的聲音',
  ]);
});

test('Chinese translation data is retained but hidden from non-Chinese target lines', () => {
  const data = neteaseToLyricsData({
    lrc: '[00:01.00]叶えたい未来がある',
    tlyric: '[00:01.00]想要实现的未来',
  }, 'en');

  assert.equal(data?.lines[0]?.translation, undefined);
  assert.equal(data?.translationsByLang?.zh?.[0], '想要實現的未來');
  assert.equal(data?.humanTranslated, false);
  assert.equal(data?.translationLang, undefined);
});
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
cd everyric2-chrome
node --test tests/netease-lyrics.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `src/lib/netease-lyrics.ts`.

- [ ] **Step 3: Implement the pure NetEase lyric converter**

Create `everyric2-chrome/src/lib/netease-lyrics.ts`:

```ts
import { Converter } from 'opencc-js';

import type { LyricsData, LyricLine } from '../types';
import { parseLRC } from './lyrics-parser';
import type { NeteaseLyric } from './netease';
import { detectLyricLanguage } from './translation-visibility';

const toTraditional = Converter({ from: 'cn', to: 'tw' });

function traditionalizeLine(line: LyricLine): void {
  line.text = toTraditional(line.text);
  for (const word of line.words ?? []) {
    word.word = toTraditional(word.word);
  }
}

export function neteaseToLyricsData(
  lyric: NeteaseLyric,
  targetLang: string,
): LyricsData | null {
  if (!lyric.lrc) return null;
  const lines = parseLRC(lyric.lrc);
  if (lines.length === 0) return null;

  if (detectLyricLanguage(lines.map(line => line.text)) === 'zh') {
    for (const line of lines) traditionalizeLine(line);
  }

  const translations = new Array<string | undefined>(lines.length);
  if (lyric.tlyric) {
    const trByTime = new Map<number, string>();
    for (const translatedLine of parseLRC(lyric.tlyric)) {
      if (translatedLine.time == null || !translatedLine.text) continue;
      trByTime.set(
        Math.round(translatedLine.time * 100),
        toTraditional(translatedLine.text),
      );
    }
    for (const [index, line] of lines.entries()) {
      if (line.time == null) continue;
      const translation = trByTime.get(Math.round(line.time * 100));
      if (!translation) continue;
      translations[index] = translation;
      if (targetLang === 'zh') line.translation = translation;
    }
  }

  const hasTranslation = translations.some(Boolean);
  const hasVisibleTranslation = hasTranslation && targetLang === 'zh';
  return {
    source: 'netease',
    synced: true,
    lines,
    plainText: lines.map(line => line.text).join('\n'),
    humanTranslated: hasVisibleTranslation,
    translationLang: hasVisibleTranslation ? 'zh' : undefined,
    availableLangs: hasTranslation ? ['zh'] : undefined,
    translationsByLang: hasTranslation ? { zh: translations } : undefined,
  };
}
```

- [ ] **Step 4: Run the conversion tests and verify GREEN**

Run:

```bash
cd everyric2-chrome
node --test tests/netease-lyrics.test.mjs
npm run typecheck
```

Expected: 4 NetEase lyric tests PASS and TypeScript exits 0.

- [ ] **Step 5: Commit the tested converter**

```bash
git add everyric2-chrome/src/lib/netease-lyrics.ts \
  everyric2-chrome/tests/netease-lyrics.test.mjs
git commit -m "feat(chrome): convert Chinese NetEase lyrics to Traditional"
```

### Task 2: Route the Service Worker Through the Shared Converter

**Files:**
- Modify: `everyric2-chrome/src/background.ts`
- Modify: `everyric2-chrome/src/lib/netease.ts`
- Modify: `everyric2-chrome/tests/netease-priority.test.mjs`

- [ ] **Step 1: Add failing centralization assertions**

Append to `everyric2-chrome/tests/netease-priority.test.mjs`:

```js
const neteaseSource = readFileSync(
  new URL('../src/lib/netease.ts', import.meta.url),
  'utf8',
);

test('NetEase lyrics use the shared language-aware Traditional converter', () => {
  assert.match(
    backgroundSource,
    /import \{ neteaseToLyricsData \} from '\.\/lib\/netease-lyrics'/,
  );
  assert.doesNotMatch(
    backgroundSource,
    /function neteaseToLyricsData\(/,
  );
  assert.doesNotMatch(
    backgroundSource,
    /const s2t = Converter/,
  );
  assert.doesNotMatch(
    neteaseSource,
    /번체 변환은\s*하지 않는다/,
  );
});
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd everyric2-chrome
node --test tests/netease-lyrics.test.mjs tests/netease-priority.test.mjs
```

Expected: FAIL because `background.ts` still contains the inline converter and `netease.ts`
still says displayed lyrics remain Simplified Chinese.

- [ ] **Step 3: Replace the background inline converter**

In `everyric2-chrome/src/background.ts`:

1. Remove:

```ts
import { Converter } from 'opencc-js';
```

2. Remove `type NeteaseLyric` from the existing `./lib/netease` import.
3. Add:

```ts
import { neteaseToLyricsData } from './lib/netease-lyrics';
```

4. Delete the complete inline block beginning with:

```ts
const s2t = Converter({ from: 'cn', to: 'tw' });
```

and ending after the local `neteaseToLyricsData()` function. Keep both existing call sites in
`FETCH_NETEASE` and `PICK_NETEASE`; the imported function has the same signature.

- [ ] **Step 4: Correct NetEase source documentation**

Replace the stale paragraph in `everyric2-chrome/src/lib/netease.ts` with:

```ts
/** 網易雲音樂歌詞來源。
 *
 * LRC 原文與 tlyric 人工翻譯會在 netease-lyrics.ts 集中處理：
 * 中文原文及中文翻譯轉為繁體，日文原文保持來源字形。
 *
 * 非官方 API 可能無預警變更或受地區限制；失敗回傳 null，讓其他來源繼續嘗試。
 */
```

- [ ] **Step 5: Run focused tests and type checking**

Run:

```bash
cd everyric2-chrome
node --test tests/netease-lyrics.test.mjs tests/netease-priority.test.mjs
npm run typecheck
```

Expected: all focused tests PASS and TypeScript exits 0.

- [ ] **Step 6: Commit service-worker wiring**

```bash
git add everyric2-chrome/src/background.ts \
  everyric2-chrome/src/lib/netease.ts \
  everyric2-chrome/tests/netease-priority.test.mjs
git commit -m "refactor(chrome): centralize NetEase lyric conversion"
```

### Task 3: Full Regression and Production Verification

**Files:**
- Modify only if a directly related verification failure requires correction.

- [ ] **Step 1: Run the full Chrome test suite**

Run:

```bash
cd everyric2-chrome
npm test
```

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run type checking and scoring self-check**

Run:

```bash
cd everyric2-chrome
npm run typecheck
node scripts/score-check.mjs
```

Expected: TypeScript exits 0 and the scoring self-check prints `score-check ok`.

- [ ] **Step 3: Build the production extension**

Run:

```bash
cd everyric2-chrome
npm run build
```

Expected: Vite completes successfully and writes `everyric2-chrome/dist`.

- [ ] **Step 4: Verify scope and repository hygiene**

Run:

```bash
git diff --check
git status --short --branch
git log -8 --oneline
```

Expected: no whitespace errors, no uncommitted files, and no artist-search, scoring,
RMVPE, Demucs, Dereverb, or unrelated UI changes in the NetEase implementation commits.

- [ ] **Step 5: Verify the user-reported examples**

Load the production extension, select a Chinese NetEase lyric result, and confirm:

```text
靜止了這世界像張照片
點不著的香菸
```

Then load a Japanese NetEase result containing `叶えたい未来がある` and confirm the original
Japanese text remains unchanged while its Chinese translation uses Traditional Chinese.
