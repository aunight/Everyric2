# Hide Same-Language Translations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide redundant translations when the source and translation are the same language across the Chrome overlay, plain lyrics, score lane, and PiP while preserving cross-language translations and pronunciation.

**Architecture:** Add one pure translation-visibility module that detects the source and translation language from the full song, returns the visible translation for each line, and exposes the same-language request guard. UI renderers consume its precomputed result; the content layer uses only the request guard so alignment-language behavior remains unchanged.

**Tech Stack:** TypeScript, Chrome Extension Manifest V3, Node.js built-in test runner, Vite

---

## File Map

- Create `everyric2-chrome/src/lib/translation-visibility.ts`: pure language detection,
  normalized duplicate comparison, and per-line visible translation selection.
- Create `everyric2-chrome/tests/translation-visibility.test.mjs`: behavioral and wiring
  regression tests.
- Modify `everyric2-chrome/src/ui/overlay.ts`: use the shared result for initial synchronized
  rendering and translation refresh.
- Modify `everyric2-chrome/src/ui/panels.ts`: use the shared result for plain lyrics.
- Modify `everyric2-chrome/src/ui/pip.ts`: store a precomputed visible translation on each pitch
  page and use it for row height and canvas rendering.
- Modify `everyric2-chrome/src/content.ts`: stop same-language translation requests with the
  display-language detector without altering `detectSongScript`.

### Task 1: Pure translation visibility rules

**Files:**
- Create: `everyric2-chrome/src/lib/translation-visibility.ts`
- Create: `everyric2-chrome/tests/translation-visibility.test.mjs`

- [ ] **Step 1: Write failing behavior tests**

Add tests that import `detectLyricLanguage`, `isSameLanguageTarget`, and
`visibleTranslations` and cover:

```js
test('hides Chinese translations under Chinese originals', () => {
  const lines = [
    { text: '我想我一直都在', translation: '我想我一直都在' },
    { text: '逃避著我的真心話', translation: '逃避著我的真心話' },
  ];
  assert.deepEqual(visibleTranslations(lines), [undefined, undefined]);
});

test('keeps Chinese translations under Japanese originals including kanji-only lines', () => {
  const lines = [
    { text: '君の声が聞こえる', translation: '我聽得見你的聲音' },
    { text: '想法', translation: '想法' },
  ];
  assert.equal(detectLyricLanguage(lines.map(line => line.text)), 'ja');
  assert.deepEqual(visibleTranslations(lines), ['我聽得見你的聲音', undefined]);
});

test('hides same-language Korean translations', () => {
  assert.deepEqual(
    visibleTranslations([{ text: '너를 사랑해', translation: '나는 너를 사랑해' }]),
    [undefined],
  );
});

test('keeps cross-language translations and hides only normalized duplicate lines', () => {
  const lines = [
    { text: 'Hello, world!', translation: '你好，世界！' },
    { text: 'Same line!', translation: ' same line ' },
  ];
  assert.deepEqual(visibleTranslations(lines), ['你好，世界！', undefined]);
});

test('keeps translations when either corpus language is unknown', () => {
  assert.deepEqual(visibleTranslations([{ text: '♪', translation: '器樂' }]), ['器樂']);
});

test('recognizes Chinese as its own translation target', () => {
  assert.equal(isSameLanguageTarget(['我想我一直都在'], 'zh'), true);
  assert.equal(isSameLanguageTarget(['君の声'], 'zh'), false);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/translation-visibility.test.mjs
```

Expected: FAIL because `src/lib/translation-visibility.ts` does not exist.

- [ ] **Step 3: Implement the minimal pure module**

Create:

```ts
import type { LyricLine } from '../types';

export type LyricLanguage = 'zh' | 'ja' | 'ko' | 'en' | 'unknown';

type TranslationLine = Pick<LyricLine, 'text' | 'translation'>;

export function detectLyricLanguage(texts: readonly string[]): LyricLanguage {
  const text = texts.join('');
  const kana = text.match(/[぀-ゟ゠-ヿ]/g)?.length ?? 0;
  const hangul = text.match(/[가-힣]/g)?.length ?? 0;
  const han = text.match(/[㐀-鿿]/g)?.length ?? 0;
  const latin = text.match(/[A-Za-z]/g)?.length ?? 0;
  if (kana > 0) return 'ja';
  if (hangul >= 2 && hangul >= han) return 'ko';
  if (han > 0) return 'zh';
  if (hangul > 0) return 'ko';
  if (latin > 0) return 'en';
  return 'unknown';
}

export function isSameLanguageTarget(texts: readonly string[], target: string): boolean {
  const source = detectLyricLanguage(texts);
  return source !== 'unknown' && source === target;
}

function comparableText(text: string): string {
  return text.normalize('NFKC').toLocaleLowerCase().replace(/[\p{P}\p{S}\s]+/gu, '');
}

export function visibleTranslations(lines: readonly TranslationLine[]): (string | undefined)[] {
  const translations = lines.map(line => line.translation?.trim()).filter(
    (text): text is string => Boolean(text),
  );
  const sourceLanguage = detectLyricLanguage(lines.map(line => line.text));
  const translationLanguage = detectLyricLanguage(translations);
  const hideSong = sourceLanguage !== 'unknown' && sourceLanguage === translationLanguage;

  return lines.map(line => {
    const translation = line.translation?.trim();
    if (!translation || hideSong) return undefined;
    const source = comparableText(line.text);
    const translated = comparableText(translation);
    return source && source === translated ? undefined : translation;
  });
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/translation-visibility.test.mjs
```

Expected: 6 tests pass, 0 fail.

### Task 2: Wire all lyrics renderers

**Files:**
- Modify: `everyric2-chrome/tests/translation-visibility.test.mjs`
- Modify: `everyric2-chrome/src/ui/overlay.ts`
- Modify: `everyric2-chrome/src/ui/panels.ts`
- Modify: `everyric2-chrome/src/ui/pip.ts`

- [ ] **Step 1: Add failing wiring tests**

Read the four source files and assert:

```js
test('all lyrics surfaces use the shared visibility rule', () => {
  const overlay = readFileSync(new URL('../src/ui/overlay.ts', import.meta.url), 'utf8');
  const panels = readFileSync(new URL('../src/ui/panels.ts', import.meta.url), 'utf8');
  const pip = readFileSync(new URL('../src/ui/pip.ts', import.meta.url), 'utf8');

  assert.ok((overlay.match(/visibleTranslations\(/g) ?? []).length >= 2);
  assert.match(panels, /visibleTranslations\(lines\)/);
  assert.match(pip, /visibleTranslation\?: string/);
  assert.match(pip, /visibleTranslations\(lines\)/);
  assert.doesNotMatch(pip, /const hasTr = pages\.some\(p => p\.line\.translation\)/);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/translation-visibility.test.mjs
```

Expected: the pure-rule tests pass and the renderer wiring test fails.

- [ ] **Step 3: Integrate the synchronized and plain lyrics views**

In `overlay.ts`, import `visibleTranslations`, compute it once in `showSyncedLyrics`, use the
indexed value instead of `line.translation`, and recompute it once in `refreshTranslations`.

In `panels.ts`, import `visibleTranslations`, compute it before the loop in `buildPlainLines`, and
render `translations[index]` instead of `line.translation`.

- [ ] **Step 4: Integrate score and PiP rendering**

In `pip.ts`:

```ts
interface PitchLine {
  line: LyricLine;
  start: number;
  end: number;
  hasNotes: boolean;
  visibleTranslation?: string;
}
```

Compute `const translations = visibleTranslations(lines)` once in `collectPitchData`, assign
`visibleTranslation: translations[i]` when constructing each page, calculate `hasTr` from
`page.visibleTranslation`, and render the active page's `visibleTranslation`.

- [ ] **Step 5: Run the focused test and typecheck**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/translation-visibility.test.mjs
npm run typecheck
```

Expected: all focused tests pass and TypeScript exits 0.

### Task 3: Stop redundant translation requests

**Files:**
- Modify: `everyric2-chrome/tests/translation-visibility.test.mjs`
- Modify: `everyric2-chrome/src/content.ts`

- [ ] **Step 1: Add a failing content wiring test**

```js
test('translation loading uses the display-language guard', () => {
  const content = readFileSync(new URL('../src/content.ts', import.meta.url), 'utf8');
  assert.match(content, /isSameLanguageTarget\(srcLines,\s*lang\)/);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/translation-visibility.test.mjs
```

Expected: only the content wiring test fails.

- [ ] **Step 3: Use the shared guard in `loadTranslations`**

Import `isSameLanguageTarget` from `./lib/translation-visibility` and replace only:

```ts
if (detectSongScript(srcLines) === lang) {
```

with:

```ts
if (isSameLanguageTarget(srcLines, lang)) {
```

Do not change `detectSongScript`, `expectsPronunciation`, alignment behavior, or stored translation
data.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/translation-visibility.test.mjs
```

Expected: all focused tests pass.

### Task 4: Full regression verification

**Files:**
- Verify only

- [ ] **Step 1: Run all Chrome tests**

Run:

```bash
cd everyric2-chrome
npm test
```

Expected: all tests pass, 0 fail.

- [ ] **Step 2: Run the production build**

Run:

```bash
cd everyric2-chrome
npm run build
```

Expected: `tsc --noEmit` and Vite build exit 0. Existing duplicate-icon or large-chunk warnings may
remain, but there must be no new errors.

- [ ] **Step 3: Review the diff and requirement checklist**

Run:

```bash
git diff --check
git diff -- everyric2-chrome/src/lib/translation-visibility.ts \
  everyric2-chrome/tests/translation-visibility.test.mjs \
  everyric2-chrome/src/ui/overlay.ts \
  everyric2-chrome/src/ui/panels.ts \
  everyric2-chrome/src/ui/pip.ts \
  everyric2-chrome/src/content.ts
```

Confirm that translation data is never deleted, pronunciation rendering is untouched, all four
surfaces use the shared rule, and `detectSongScript` remains unchanged.

## Commit Policy

The working tree already contains extensive user-owned and prior-session changes in the renderer
files. Commit this plan independently, but do not stage or commit overlapping implementation files
unless their pre-existing changes can be separated without risk. Report implementation changes and
verification evidence without claiming unrelated dirty files.
