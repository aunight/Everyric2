# Chrome Title and Settings Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correctly display Chinese song titles and keep the settings return button visible with blur clipped to the button.

**Architecture:** Move pure title parsing into a dependency-free module consumed by `song-detector.ts`. Lock the visual behavior with Node source-contract tests, then adjust the existing CSS stacking contexts without changing the overlay DOM.

**Tech Stack:** TypeScript, Node test runner, CSS, Vite, Chrome Extension Manifest V3

---

### Task 1: Add executable Chrome unit tests

**Files:**
- Modify: `everyric2-chrome/package.json`
- Create: `everyric2-chrome/tests/song-title.test.mjs`
- Create: `everyric2-chrome/tests/settings-css.test.mjs`

- [ ] **Step 1: Add the title parser failing test**

Create `tests/song-title.test.mjs` using `node:test` and import the wished-for API:

```js
import assert from 'node:assert/strict';
import test from 'node:test';

import { parseSongTitle } from '../src/lib/song-title.ts';

test('extracts a Chinese title and drops its English translation and promo suffix', () => {
  assert.deepEqual(
    parseSongTitle(
      '李浩瑋 Howard Lee【真心話 From The Bottom Of Your Heart】 Official Music Video(4K)',
    ),
    { title: '真心話', artist: '李浩瑋 Howard Lee' },
  );
});

test('preserves established artist separators', () => {
  assert.deepEqual(
    parseSongTitle('宇多田ヒカル - First Love (Official Video)'),
    { title: 'First Love', artist: '宇多田ヒカル' },
  );
});

test('does not delete an ordinary English title', () => {
  assert.deepEqual(parseSongTitle('The Beatles - Let It Be'), {
    title: 'Let It Be',
    artist: 'The Beatles',
  });
});
```

- [ ] **Step 2: Add the settings CSS failing test**

Create `tests/settings-css.test.mjs`. Read `public/overlay.css`, extract exact selector bodies, and assert:

```js
assert.ok(settingsZ > chipsZ, 'settings sheet must cover language chips');
assert.doesNotMatch(settingsTop, /backdrop-filter|background\s*:/);
assert.match(settingsBack, /backdrop-filter\s*:/);
assert.match(settingsBack, /border-radius\s*:\s*999px/);
```

- [ ] **Step 3: Register the test command**

Add this script without changing the existing build scripts:

```json
"test": "node --experimental-strip-types --test tests/*.test.mjs"
```

- [ ] **Step 4: Run tests and verify RED**

Run:

```bash
cd everyric2-chrome
npm test
```

Expected: title test fails because `src/lib/song-title.ts` does not exist; CSS test fails because `.ey-settings` is below `.ey-lang-chips` and blur is on `.ey-settings-top`.

### Task 2: Implement structural song-title parsing

**Files:**
- Create: `everyric2-chrome/src/lib/song-title.ts`
- Modify: `everyric2-chrome/src/lib/song-detector.ts`

- [ ] **Step 1: Implement promo cleanup and parsing**

Create a pure module with:

```ts
export interface ParsedSongTitle {
  title: string;
  artist: string | null;
}

export function parseSongTitle(raw: string): ParsedSongTitle
```

Implementation order:

1. Repeatedly remove bracketed known noise and trailing promo phrases such as `Official Music Video`,
   `Official Video`, `Lyrics Video`, `Official Audio`, `MV`, `4K`, `HD`, and `HQ`.
2. Extract `artist【title】`, `artist「title」`, `artist『title』`, or `artist《title》`.
3. Otherwise split the first existing separator from ` - `, ` – `, ` — `, or ` | `.
4. For a candidate containing Han characters but no kana or Hangul, replace
   `^(.+[\p{Script=Han}\d])\s+[A-Za-z].*$` with group 1.
5. Normalize repeated whitespace and trim punctuation left by removed promo text.

- [ ] **Step 2: Route every detector source through the parser**

Replace the local `TITLE_NOISE`, `cleanTitle`, and `splitArtistTitle` responsibilities in
`song-detector.ts` with `parseSongTitle()`:

```ts
const parsed = parseSongTitle(meta.title);
return {
  title: parsed.title,
  artist: parsed.artist ?? (meta.artist || null),
  videoId,
  duration,
};
```

Use the same pattern for YouTube Music and normal YouTube DOM, retaining their byline/channel fallback.

- [ ] **Step 3: Run title tests and verify GREEN**

Run:

```bash
cd everyric2-chrome
npm test -- --test-name-pattern="title|English|separator"
```

Expected: all title parser tests pass.

### Task 3: Fix the settings return button stacking and blur

**Files:**
- Modify: `everyric2-chrome/public/overlay.css`

- [ ] **Step 1: Raise the settings sheet**

Change `.ey-settings` from `z-index: 3` to `z-index: 5`, above `.ey-lang-chips { z-index: 4; }`.

- [ ] **Step 2: Move the glass effect onto the button**

Use:

```css
.ey-settings-top {
  position: sticky;
  top: 0;
  z-index: 3;
  padding: 6px 0;
}

.ey-settings-back {
  width: 100%;
  border-radius: 999px;
  overflow: hidden;
  background: color-mix(in srgb, var(--ey-bg, #111) 62%, transparent);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}
```

- [ ] **Step 3: Run CSS tests and verify GREEN**

Run:

```bash
cd everyric2-chrome
npm test -- --test-name-pattern="settings"
```

Expected: settings sheet is above chips, outer sticky row is transparent, and the button owns the clipped blur.

### Task 4: Verify the Chrome extension

**Files:**
- Verify only

- [ ] **Step 1: Run all Chrome tests**

Run `npm test` in `everyric2-chrome`; expected: zero failures.

- [ ] **Step 2: Run the production build**

Run `npm run build` in `everyric2-chrome`; expected: TypeScript and Vite exit 0.

- [ ] **Step 3: Reload the unpacked extension and inspect**

Reload the Everyric2 extension in Chrome, reopen the settings sheet, and confirm:

- the return button is visible directly below the fixed header;
- language chips do not cover the settings sheet;
- blur is clipped to the pill button;
- the example video displays title `真心話` and artist `李浩瑋 Howard Lee`.

Do not commit the implementation files automatically because `song-detector.ts`, `overlay.css`, and
`package.json` already contain unrelated user-owned changes.
