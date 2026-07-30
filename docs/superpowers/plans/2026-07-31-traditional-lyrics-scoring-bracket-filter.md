# Traditional Lyrics and Scoring Bracket Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Chinese lyrics from every Chrome data source to Traditional Chinese at the common display boundary and remove all balanced bracketed lyric segments from the PiP scoring window without changing the normal lyrics panel.

**Architecture:** Add two pure modules: one normalizes Chinese fields in `LyricsData`, and one creates an index-preserving scoring-only copy of `LyricLine[]`. Wire the first into `applyLyricsData()` before `currentData` assignment, reuse its OpenCC converter in the NetEase adapter, and wire the second inside `PipController.setLines()` so the playback engine and PiP retain identical line indices.

**Tech Stack:** TypeScript, opencc-js, Node test runner, Chrome extension, Vite

---

## File Structure

- Create `everyric2-chrome/src/lib/traditional-lyrics.ts`: pure Traditional Chinese normalization for any `LyricsData`.
- Create `everyric2-chrome/tests/traditional-lyrics.test.mjs`: behavior and common-boundary wiring tests.
- Modify `everyric2-chrome/src/lib/netease-lyrics.ts`: reuse the common OpenCC converter.
- Modify `everyric2-chrome/src/content.ts`: normalize every loaded source before assigning `currentData`.
- Create `everyric2-chrome/src/lib/scoring-lyrics.ts`: pure, index-preserving bracket removal for PiP scoring data.
- Create `everyric2-chrome/tests/scoring-lyrics.test.mjs`: bracket, immutability, and PiP wiring tests.
- Modify `everyric2-chrome/src/ui/pip.ts`: store and score only the sanitized PiP copy.

### Task 1: Add Source-Agnostic Traditional Chinese Normalization

**Files:**
- Create: `everyric2-chrome/src/lib/traditional-lyrics.ts`
- Create: `everyric2-chrome/tests/traditional-lyrics.test.mjs`

- [ ] **Step 1: Write failing normalization tests**

Create `everyric2-chrome/tests/traditional-lyrics.test.mjs`:

```js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { normalizeTraditionalLyricsData } from '../src/lib/traditional-lyrics.ts';

const contentSource = readFileSync(new URL('../src/content.ts', import.meta.url), 'utf8');
const neteaseSource = readFileSync(
  new URL('../src/lib/netease-lyrics.ts', import.meta.url),
  'utf8',
);

test('Everyric Chinese cache data becomes Traditional without mutating the input', () => {
  const input = {
    source: 'everyric',
    synced: true,
    plainText: '静止了这世界像张照片\n点不着的香烟',
    lines: [
      {
        time: 1,
        endTime: 2,
        text: '静止了这世界像张照片',
        words: [
          { word: '静止', start: 1, end: 1.4 },
          { word: '这世界像张照片', start: 1.4, end: 2 },
        ],
        notes: [{ midi: 60, start: 1, end: 2 }],
      },
      {
        time: 2,
        endTime: 3,
        text: '点不着的香烟',
      },
    ],
  };
  const before = structuredClone(input);

  const output = normalizeTraditionalLyricsData(input);

  assert.deepEqual(output.lines.map(line => line.text), [
    '靜止了這世界像張照片',
    '點不著的香菸',
  ]);
  assert.deepEqual(output.lines[0].words.map(word => word.word), [
    '靜止',
    '這世界像張照片',
  ]);
  assert.equal(output.plainText, '靜止了這世界像張照片\n點不著的香菸');
  assert.deepEqual(output.lines[0].notes, input.lines[0].notes);
  assert.deepEqual(input, before);
});

test('Japanese originals stay unchanged while explicit Chinese layers become Traditional', () => {
  const output = normalizeTraditionalLyricsData({
    source: 'everyric',
    synced: true,
    plainText: '叶えたい未来がある',
    translationLang: 'zh',
    lines: [{
      time: 1,
      endTime: 2,
      text: '叶えたい未来がある',
      words: [{ word: '未来', start: 1.3, end: 1.7 }],
      translation: '想要实现的未来',
    }],
    translationsByLang: {
      zh: ['想要实现的未来'],
      en: ['There is a future I want'],
    },
  });

  assert.equal(output.lines[0].text, '叶えたい未来がある');
  assert.equal(output.lines[0].words[0].word, '未来');
  assert.equal(output.lines[0].translation, '想要實現的未來');
  assert.deepEqual(output.translationsByLang.zh, ['想要實現的未來']);
  assert.deepEqual(output.translationsByLang.en, ['There is a future I want']);
});

test('unknown or non-Chinese originals are not rewritten', () => {
  const input = {
    source: 'caption',
    synced: false,
    plainText: 'hello world',
    lines: [{ time: null, endTime: null, text: 'hello world' }],
  };

  assert.deepEqual(normalizeTraditionalLyricsData(input), input);
});
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/traditional-lyrics.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `src/lib/traditional-lyrics.ts`.

- [ ] **Step 3: Implement the pure normalizer**

Create `everyric2-chrome/src/lib/traditional-lyrics.ts`:

```ts
import { Converter } from 'opencc-js';

import type { LyricsData, LyricLine } from '../types';
import { detectLyricLanguage } from './translation-visibility.ts';

const toTraditional = Converter({ from: 'cn', to: 'tw' });

export function toTraditionalText(text: string): string {
  return toTraditional(text);
}

function normalizeLine(
  line: LyricLine,
  sourceIsChinese: boolean,
  visibleTranslationIsChinese: boolean,
): LyricLine {
  return {
    ...line,
    text: sourceIsChinese ? toTraditionalText(line.text) : line.text,
    words: line.words?.map(word => ({
      ...word,
      word: sourceIsChinese ? toTraditionalText(word.word) : word.word,
    })),
    translation: line.translation && visibleTranslationIsChinese
      ? toTraditionalText(line.translation)
      : line.translation,
  };
}

export function normalizeTraditionalLyricsData(data: LyricsData): LyricsData {
  const sourceIsChinese =
    detectLyricLanguage(data.lines.map(line => line.text)) === 'zh';
  const visibleTranslationIsChinese = data.translationLang === 'zh';
  const lines = data.lines.map(line =>
    normalizeLine(line, sourceIsChinese, visibleTranslationIsChinese));

  const translationsByLang = data.translationsByLang
    ? Object.fromEntries(
      Object.entries(data.translationsByLang).map(([lang, values]) => [
        lang,
        lang === 'zh'
          ? values.map(value => value ? toTraditionalText(value) : value)
          : [...values],
      ]),
    )
    : undefined;

  return {
    ...data,
    lines,
    plainText: sourceIsChinese
      ? lines.map(line => line.text).join('\n')
      : data.plainText,
    ...(translationsByLang ? { translationsByLang } : {}),
  };
}
```

- [ ] **Step 4: Run the focused tests and type check**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/traditional-lyrics.test.mjs
npm run typecheck
```

Expected: 3 tests PASS and TypeScript exits 0.

- [ ] **Step 5: Commit the pure module**

```bash
git add everyric2-chrome/src/lib/traditional-lyrics.ts \
  everyric2-chrome/tests/traditional-lyrics.test.mjs
git commit -m "feat(chrome): normalize Chinese lyrics to Traditional"
```

### Task 2: Wire Every Lyrics Source Through the Common Normalizer

**Files:**
- Modify: `everyric2-chrome/tests/traditional-lyrics.test.mjs`
- Modify: `everyric2-chrome/src/lib/netease-lyrics.ts`
- Modify: `everyric2-chrome/src/content.ts`

- [ ] **Step 1: Add failing common-boundary wiring tests**

Append to `everyric2-chrome/tests/traditional-lyrics.test.mjs`:

```js
test('all loaded lyrics normalize before currentData assignment', () => {
  assert.match(
    contentSource,
    /import \{ normalizeTraditionalLyricsData \} from '\.\/lib\/traditional-lyrics\.ts'/,
  );
  assert.match(
    contentSource,
    /function applyLyricsData[\s\S]*?stripProductionCredits\(data\)[\s\S]*?normalizeTraditionalLyricsData\(data\)[\s\S]*?currentData = data/,
  );
});

test('NetEase reuses the common OpenCC converter', () => {
  assert.match(
    neteaseSource,
    /import \{ toTraditionalText \} from '\.\/traditional-lyrics\.ts'/,
  );
  assert.doesNotMatch(neteaseSource, /Converter\(\{ from: 'cn', to: 'tw' \}\)/);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/traditional-lyrics.test.mjs
```

Expected: the two new wiring tests FAIL because `content.ts` and `netease-lyrics.ts` do not import the shared helper.

- [ ] **Step 3: Reuse the shared converter in NetEase**

In `everyric2-chrome/src/lib/netease-lyrics.ts`, remove:

```ts
import { Converter } from 'opencc-js';
const toTraditional = Converter({ from: 'cn', to: 'tw' });
```

Add:

```ts
import { toTraditionalText } from './traditional-lyrics.ts';
```

Replace every `toTraditional(...)` call with `toTraditionalText(...)`.

- [ ] **Step 4: Normalize at the common display boundary**

In `everyric2-chrome/src/content.ts`, add:

```ts
import { normalizeTraditionalLyricsData } from './lib/traditional-lyrics.ts';
```

Change the start of `applyLyricsData()` to:

```ts
function applyLyricsData(data: LyricsData | null): void {
  const panel = ensureOverlay();
  if (data) {
    data = stripProductionCredits(data);
    if (data.lines.length > 0) {
      data = normalizeTraditionalLyricsData(data);
    } else {
      data = null;
    }
  }
  currentData = data;
```

- [ ] **Step 5: Run NetEase, Traditional, and type tests**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test \
  tests/traditional-lyrics.test.mjs \
  tests/netease-lyrics.test.mjs \
  tests/netease-priority.test.mjs
npm run typecheck
```

Expected: all focused tests PASS and TypeScript exits 0.

- [ ] **Step 6: Commit the common-boundary wiring**

```bash
git add everyric2-chrome/src/content.ts \
  everyric2-chrome/src/lib/netease-lyrics.ts \
  everyric2-chrome/tests/traditional-lyrics.test.mjs
git commit -m "fix(chrome): traditionalize cached Everyric lyrics"
```

### Task 3: Build the Index-Preserving Scoring Bracket Sanitizer

**Files:**
- Create: `everyric2-chrome/src/lib/scoring-lyrics.ts`
- Create: `everyric2-chrome/tests/scoring-lyrics.test.mjs`

- [ ] **Step 1: Write failing scoring sanitizer tests**

Create `everyric2-chrome/tests/scoring-lyrics.test.mjs`:

```js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  lyricsForScoring,
  stripScoringBrackets,
} from '../src/lib/scoring-lyrics.ts';

const pipSource = readFileSync(new URL('../src/ui/pip.ts', import.meta.url), 'utf8');

test('removes every supported balanced bracket style', () => {
  assert.equal(
    stripScoringBrackets('主歌 (echo) （和聲） [English] 【口號】 尾聲'),
    '主歌 尾聲',
  );
});

test('keeps unmatched brackets to avoid deleting normal lyrics', () => {
  assert.equal(stripScoringBrackets('歌詞（未完'), '歌詞（未完');
  assert.equal(stripScoringBrackets('歌詞 ending)'), '歌詞 ending)');
});

test('bracket-only scoring lines become invisible index placeholders', () => {
  const input = [{
    time: 1,
    endTime: 2,
    text: "(We don't we don't we don't)",
    words: [
      { word: '(', start: 1, end: 1.05 },
      { word: "We don't we don't we don't", start: 1.05, end: 1.95 },
      { word: ')', start: 1.95, end: 2 },
    ],
    notes: [{ midi: 60, start: 1, end: 2 }],
    translation: '（重複和聲）',
  }];
  const before = structuredClone(input);

  const output = lyricsForScoring(input);

  assert.equal(output.length, input.length);
  assert.equal(output[0].time, 1);
  assert.equal(output[0].text, '');
  assert.deepEqual(output[0].words, []);
  assert.deepEqual(output[0].notes, []);
  assert.equal(output[0].translation, undefined);
  assert.equal(output[0].pronunciation, undefined);
  assert.deepEqual(output[0].pronSegments, []);
  assert.deepEqual(input, before);
});

test('mixed lines keep only words outside bracket spans', () => {
  const output = lyricsForScoring([{
    time: 1,
    endTime: 3,
    text: 'Forever (yeah) tonight 【echo】',
    words: [
      { word: 'Forever', start: 1, end: 1.5 },
      { word: '(yeah)', start: 1.5, end: 2 },
      { word: 'tonight', start: 2, end: 2.5 },
      { word: '【echo】', start: 2.5, end: 3 },
    ],
    notes: [
      { midi: 60, start: 1, end: 1.5 },
      { midi: 62, start: 1.5, end: 2 },
      { midi: 64, start: 2, end: 2.5 },
    ],
    translation: '永遠（耶）直到今晚',
  }]);

  assert.equal(output[0].text, 'Forever tonight');
  assert.deepEqual(output[0].words.map(word => word.word), ['Forever', 'tonight']);
  assert.equal(output[0].translation, '永遠直到今晚');
  assert.equal(output[0].notes.length, 3);
});
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/scoring-lyrics.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `src/lib/scoring-lyrics.ts`.

- [ ] **Step 3: Implement bracket masking and scoring copies**

Create `everyric2-chrome/src/lib/scoring-lyrics.ts`:

```ts
import type { LyricLine, WordSegment } from '../types';

const BRACKET_PATTERNS = [
  /\([^()]*\)/gu,
  /（[^（）]*）/gu,
  /\[[^\[\]]*\]/gu,
  /【[^【】]*】/gu,
];

function bracketMask(text: string): boolean[] {
  const mask = new Array<boolean>(text.length).fill(false);
  for (const pattern of BRACKET_PATTERNS) {
    for (const match of text.matchAll(pattern)) {
      const start = match.index;
      const end = start + match[0].length;
      for (let index = start; index < end; index += 1) {
        mask[index] = true;
      }
    }
  }
  return mask;
}

function textOutsideMask(text: string, mask: boolean[]): string {
  let result = '';
  for (let index = 0; index < text.length; index += 1) {
    if (!mask[index]) result += text[index];
  }
  return result.replace(/\s{2,}/gu, ' ').trim();
}

export function stripScoringBrackets(text: string): string {
  return textOutsideMask(text, bracketMask(text));
}

function wordsOutsideMask(
  lineText: string,
  words: WordSegment[] | undefined,
  mask: boolean[],
): WordSegment[] | undefined {
  if (!words) return undefined;
  let cursor = 0;
  const result: WordSegment[] = [];

  for (const word of words) {
    let kept = '';
    for (const character of word.word) {
      const position = lineText.indexOf(character, cursor);
      if (position < 0) {
        kept += character;
        continue;
      }
      cursor = position + character.length;
      const hidden = mask
        .slice(position, position + character.length)
        .some(Boolean);
      if (!hidden) kept += character;
    }
    kept = kept.replace(/\s{2,}/gu, ' ').trim();
    if (kept) result.push({ ...word, word: kept });
  }

  return result;
}

function optionalScoringText(value: string | undefined): string | undefined {
  if (!value) return value;
  return stripScoringBrackets(value) || undefined;
}

export function lyricsForScoring(lines: LyricLine[]): LyricLine[] {
  return lines.map(line => {
    const mask = bracketMask(line.text);
    const text = textOutsideMask(line.text, mask);
    if (text === line.text) return line;
    if (!text) {
      return {
        ...line,
        text: '',
        words: [],
        notes: [],
        translation: undefined,
        pronunciation: undefined,
        pronSegments: [],
        pron: undefined,
        pronSegsByScript: undefined,
      };
    }
    return {
      ...line,
      text,
      words: wordsOutsideMask(line.text, line.words, mask),
      translation: optionalScoringText(line.translation),
      pronunciation: optionalScoringText(line.pronunciation),
    };
  });
}
```

- [ ] **Step 4: Run the sanitizer tests and type check**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/scoring-lyrics.test.mjs
npm run typecheck
```

Expected: 4 tests PASS and TypeScript exits 0.

- [ ] **Step 5: Commit the pure scoring sanitizer**

```bash
git add everyric2-chrome/src/lib/scoring-lyrics.ts \
  everyric2-chrome/tests/scoring-lyrics.test.mjs
git commit -m "feat(chrome): remove brackets from scoring lyrics"
```

### Task 4: Wire Sanitized Lyrics Only Into the PiP Scoring Window

**Files:**
- Modify: `everyric2-chrome/tests/scoring-lyrics.test.mjs`
- Modify: `everyric2-chrome/src/ui/pip.ts`

- [ ] **Step 1: Add a failing PiP wiring test**

Append to `everyric2-chrome/tests/scoring-lyrics.test.mjs`:

```js
test('PiP stores the scoring-only copy while normal content keeps original lines', () => {
  assert.match(
    pipSource,
    /import \{ lyricsForScoring \} from '\.\.\/lib\/scoring-lyrics\.ts'/,
  );
  assert.match(
    pipSource,
    /setLines\(lines: LyricLine\[\]\): void \{[\s\S]*?const scoringLines = lyricsForScoring\(lines\)[\s\S]*?this\.lines = scoringLines[\s\S]*?collectPitchData\(scoringLines,/,
  );
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/scoring-lyrics.test.mjs
```

Expected: the new wiring test FAILS because `pip.ts` does not import or call `lyricsForScoring`.

- [ ] **Step 3: Sanitize inside `PipController.setLines()`**

In `everyric2-chrome/src/ui/pip.ts`, add:

```ts
import { lyricsForScoring } from '../lib/scoring-lyrics.ts';
```

Change `setLines()` to begin:

```ts
  setLines(lines: LyricLine[]): void {
    const scoringLines = lyricsForScoring(lines);
    this.lines = scoringLines;
    this.index = -1;
    this.songLanguage = detectLyricLanguage(scoringLines.map(line => line.text));
    this.pitch = collectPitchData(scoringLines, this.pronScript, this.songLanguage);
```

Do not change `content.ts` calls such as `panel.showSyncedLyrics(data.lines, ...)`; those must keep receiving the original, bracket-preserving lines.

- [ ] **Step 4: Run all relevant scoring tests**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test \
  tests/scoring-lyrics.test.mjs \
  tests/pitch-ui.test.mjs \
  tests/scoring-ui.test.mjs \
  tests/score-store.test.mjs
npm run typecheck
```

Expected: all focused scoring tests PASS and TypeScript exits 0.

- [ ] **Step 5: Commit the PiP wiring**

```bash
git add everyric2-chrome/src/ui/pip.ts \
  everyric2-chrome/tests/scoring-lyrics.test.mjs
git commit -m "fix(chrome): hide bracketed lyrics in scoring view"
```

### Task 5: Full Chrome Verification

**Files:**
- Verify only; no production files expected.

- [ ] **Step 1: Run the full Chrome test suite**

```bash
cd everyric2-chrome
npm test
```

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run type and scoring checks**

```bash
cd everyric2-chrome
npm run typecheck
node scripts/score-check.mjs
```

Expected: TypeScript exits 0 and prints `score-check ok`.

- [ ] **Step 3: Build the production extension**

```bash
cd everyric2-chrome
npm run build
```

Expected: Vite finishes with `✓ built` and exit code 0.

- [ ] **Step 4: Check repository cleanliness**

```bash
cd ..
git diff --check
git status --short
```

Expected: no whitespace errors and no uncommitted files.

- [ ] **Step 5: Inspect the final commit range**

```bash
git log --oneline 8c9f06a..HEAD
```

Expected: only the Traditional normalization and scoring-bracket commits from this plan.
