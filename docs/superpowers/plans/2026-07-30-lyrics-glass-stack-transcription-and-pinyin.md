# Lyrics Glass Stack, Transcription Feedback, and Pinyin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let lyrics scroll beneath individually blurred top controls, turn the yellow AI action row into an immediate purple progress row, and label Chinese pronunciation as Romanized Pinyin.

**Architecture:** Keep `.ey-body` as the only scroller and move all upper controls into an absolutely positioned `.ey-top-stack`. Measure that stack with `ResizeObserver` and expose its height through a CSS variable used as the body's initial top padding. Keep prompt and generation elements separate but mutually exclusive in the same stack position, and isolate song-aware pronunciation-chip behavior in a pure helper.

**Tech Stack:** TypeScript, Shadow DOM, CSS backdrop filters, Node test runner, Vite

---

## File Map

- Create `everyric2-chrome/src/ui/pronunciation-chip.ts`: pure song-aware label and click-state rules.
- Create `everyric2-chrome/tests/pronunciation-chip.test.mjs`: Chinese Pinyin and existing-language regression tests.
- Create `everyric2-chrome/tests/overlay-glass-stack.test.mjs`: DOM/CSS contract checks for the floating stack and prompt/progress state.
- Modify `everyric2-chrome/src/ui/overlay.ts`: build and measure the top stack, switch prompt/progress visibility, and use song-aware pronunciation rules.
- Modify `everyric2-chrome/src/content.ts`: tell Overlay whether the visible generation state belongs to the current video.
- Modify `everyric2-chrome/public/overlay.css`: floating stack, dynamic body inset, individual glass surfaces, purple generation row, collapsed/settings layering.
- Modify locale `messages.json` files: add the Pinyin chip label.

### Task 1: Chinese Pronunciation Chip Contract

**Files:**
- Create: `everyric2-chrome/src/ui/pronunciation-chip.ts`
- Create: `everyric2-chrome/tests/pronunciation-chip.test.mjs`
- Modify: `everyric2-chrome/src/ui/overlay.ts`
- Modify: `everyric2-chrome/_locales/en/messages.json`
- Modify: `everyric2-chrome/_locales/ja/messages.json`
- Modify: `everyric2-chrome/_locales/ko/messages.json`
- Modify: `everyric2-chrome/_locales/zh_TW/messages.json`

- [ ] **Step 1: Write the failing pure-function tests**

```js
import assert from 'node:assert/strict';
import test from 'node:test';

import { nextPronunciationPatch, pronunciationChipKey } from '../src/ui/pronunciation-chip.ts';

test('Chinese songs identify visible pronunciation as Pinyin', () => {
  assert.equal(pronunciationChipKey('zh', true, 'hangul'), 'pinyin');
  assert.deepEqual(nextPronunciationPatch('zh', false, 'hangul'), {
    showPronunciation: true,
    pronunciationScript: 'romaji',
  });
  assert.deepEqual(nextPronunciationPatch('zh', true, 'romaji'), {
    showPronunciation: false,
  });
});

test('existing English and Japanese chip behavior remains available', () => {
  assert.equal(pronunciationChipKey('en', true, 'romaji'), 'kk');
  assert.equal(pronunciationChipKey('ja', true, 'kana'), 'kana');
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `cd everyric2-chrome && npm test`

Expected: FAIL because `src/ui/pronunciation-chip.ts` does not exist.

- [ ] **Step 3: Implement the focused helper**

```ts
import type { PronScript } from '../lib/lang';
import type { Settings } from '../types';

type PronunciationPatch = Pick<Settings, 'showPronunciation'> &
  Partial<Pick<Settings, 'pronunciationScript'>>;

export function pronunciationChipKey(
  songLang: string,
  show: boolean,
  script: PronScript,
): 'off' | 'kk' | 'pinyin' | PronScript {
  if (!show) return 'off';
  if (songLang === 'en') return 'kk';
  if (songLang === 'zh') return 'pinyin';
  return script;
}

export function nextPronunciationPatch(
  songLang: string,
  show: boolean,
  script: PronScript,
): PronunciationPatch {
  if (songLang === 'zh') {
    return show
      ? { showPronunciation: false }
      : { showPronunciation: true, pronunciationScript: 'romaji' };
  }
  if (songLang === 'en') return { showPronunciation: !show };
  if (!show) return { showPronunciation: true, pronunciationScript: 'hangul' };
  const order: PronScript[] = ['hangul', 'romaji', 'kana'];
  const index = order.indexOf(script);
  return index >= order.length - 1
    ? { showPronunciation: false }
    : { showPronunciation: true, pronunciationScript: order[index + 1] };
}
```

Wire `overlay.ts` click handling through `nextPronunciationPatch(...)` and label rendering through
`pronunciationChipKey(...)`. Map `pinyin` to a new `overlay.pronChip.pinyin` locale key with values
`Pinyin`, `拼音`, `병음`, and `羅馬拼音` in the four locale files.

- [ ] **Step 4: Run focused tests and typecheck**

Run: `cd everyric2-chrome && npm test && npm run typecheck`

Expected: all tests pass and TypeScript exits 0.

### Task 2: Floating Top-Stack Contract

**Files:**
- Create: `everyric2-chrome/tests/overlay-glass-stack.test.mjs`
- Modify: `everyric2-chrome/src/ui/overlay.ts`
- Modify: `everyric2-chrome/public/overlay.css`

- [ ] **Step 1: Write failing DOM/CSS contract tests**

The test reads `overlay.ts` and `overlay.css` and asserts:

```js
assert.match(source, /className:\s*'ey-top-stack'/);
assert.match(source, /topStackResizeObserver\.observe\(this\.topStack\)/);
assert.match(source, /--ey-top-stack-height/);
assert.doesNotMatch(source, /ey-under-chips/);

assert.match(topStackCss, /position\s*:\s*absolute/);
assert.match(topStackCss, /z-index\s*:\s*4/);
assert.match(bodyCss, /--ey-top-stack-height/);
assert.match(headerCss, /backdrop-filter/);
assert.match(chipsCss, /backdrop-filter/);
assert.match(bannerCss, /backdrop-filter/);
assert.doesNotMatch(css, /\.ey-body\.ey-under-chips/);
```

Also assert `.ey-settings` has a greater numeric z-index than `.ey-top-stack`.

- [ ] **Step 2: Run the test and verify RED**

Run: `cd everyric2-chrome && npm test`

Expected: FAIL because `.ey-top-stack` and `--ey-top-stack-height` are absent.

- [ ] **Step 3: Build and measure the top stack**

In `Overlay`, add:

```ts
private topStack: HTMLDivElement;
private topStackResizeObserver: ResizeObserver;
```

Wrap upper controls:

```ts
this.topStack = h(
  'div',
  { className: 'ey-top-stack' },
  this.header,
  this.langChipsRow,
  this.serverBar,
  this.banner,
  this.genChip,
  this.genList,
  this.noticeChip,
  this.warnBar,
  this.translationPendingBar,
);
this.panel = h(
  'div',
  { className: 'ey-panel' },
  this.topStack,
  this.body,
  this.resumeChip,
  this.footer,
  this.debugStrip,
  this.debugPanelEl,
);
```

After mounting, observe the actual stack:

```ts
this.topStackResizeObserver = new ResizeObserver(() => {
  this.panel.style.setProperty(
    '--ey-top-stack-height',
    `${this.topStack.getBoundingClientRect().height}px`,
  );
});
this.topStackResizeObserver.observe(this.topStack);
```

Disconnect it in `destroy()`. Remove all `ey-under-chips` class mutations.

- [ ] **Step 4: Implement floating and collapsed CSS**

Use these structural rules:

```css
.ey-top-stack {
  position: absolute;
  inset: 0 0 auto;
  z-index: 4;
  display: flex;
  flex-direction: column;
  pointer-events: none;
}
.ey-top-stack > * { pointer-events: auto; }
.ey-body {
  padding: calc(var(--ey-top-stack-height, 0px) + 8px) 8px 10px;
  scroll-padding-top: calc(var(--ey-top-stack-height, 0px) + 8px);
}
.ey-settings { z-index: 5; }
.ey-panel.collapsed .ey-top-stack { position: relative; }
.ey-panel.collapsed .ey-top-stack > :not(.ey-header) { display: none !important; }
```

Give `.ey-header`, `.ey-lang-chips`, and `.ey-banner` separate translucent backgrounds, rounded
clipping, `backdrop-filter`, and `-webkit-backdrop-filter`. Preserve a readable opaque fallback
color in each background.

- [ ] **Step 5: Run tests and typecheck**

Run: `cd everyric2-chrome && npm test && npm run typecheck`

Expected: all tests pass and TypeScript exits 0.

### Task 3: Immediate Purple Transcription State

**Files:**
- Modify: `everyric2-chrome/tests/overlay-glass-stack.test.mjs`
- Modify: `everyric2-chrome/src/ui/overlay.ts`
- Modify: `everyric2-chrome/src/content.ts`
- Modify: `everyric2-chrome/public/overlay.css`

- [ ] **Step 1: Extend the failing contract test**

Assert the source and CSS contain:

```js
assert.match(source, /setGenerationChip\([^)]*currentActive/);
assert.match(source, /ey-current-generation/);
assert.match(source, /bannerHiddenForGeneration/);
assert.match(currentGenerationCss, /rgba\(124,\s*92,\s*255/);
assert.match(currentGenerationCss, /backdrop-filter/);
assert.match(currentGenerationCss, /border-radius/);
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `cd everyric2-chrome && node --experimental-strip-types --test tests/overlay-glass-stack.test.mjs`

Expected: FAIL because current-generation state is absent.

- [ ] **Step 3: Add prompt/progress mutual exclusion**

Extend Overlay state:

```ts
private bannerHiddenForGeneration = false;
```

Change the method signature to:

```ts
setGenerationChip(text: string | null, cancellable = false, currentActive = false): void
```

When `currentActive` is true, remember whether the prompt was visible, hide it, and add
`.ey-current-generation` to the generation element. When it becomes false, remove the class and
restore the remembered prompt. Clear the remembered flag in `resetBody()`.

In `content.ts`, compute:

```ts
const currentPreparing = Boolean(
  currentVideoId && preparingGenerate.has(currentVideoId),
);
const currentActive = Boolean(cur) || currentPreparing;
overlay?.setGenerationChip(text, Boolean(cur), currentActive);
```

This uses the existing immediate `preparingGenerate.add(videoId); updateGenChip();` call, so no
server response is needed before the purple state appears.

- [ ] **Step 4: Style the current generation row**

```css
.ey-gen-chip.ey-current-generation {
  align-self: stretch;
  margin: 4px 8px 0;
  padding: 8px 12px;
  border: 1px solid rgba(154, 128, 255, 0.35);
  border-radius: 12px;
  background: rgba(124, 92, 255, 0.24);
  backdrop-filter: blur(14px) saturate(1.2);
  -webkit-backdrop-filter: blur(14px) saturate(1.2);
}
```

Keep the existing cancel button and queue-list click handlers.

- [ ] **Step 5: Run all extension checks**

Run: `cd everyric2-chrome && npm test && npm run build`

Expected: all Node tests pass; TypeScript and Vite production build exit 0.

### Task 4: Regression and Live Acceptance

**Files:**
- Verify only; do not create new production files.

- [ ] **Step 1: Run whitespace and change-scope checks**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended files plus the user's pre-existing dirty files remain.

- [ ] **Step 2: Reload the unpacked extension**

Open `chrome://extensions`, reload Everyric2, then refresh the existing YouTube music tab. This step
requires the user if Chrome blocks automation of internal pages.

- [ ] **Step 3: Verify the visible layout**

Confirm:

1. The first lyric is fully visible below the top controls at rest.
2. Scrolling moves lyrics behind yellow AI, language, and title glass surfaces without hard clipping.
3. Each surface has its own rounded, translucent blur.
4. The settings return button remains visible and clickable.
5. The Chinese pronunciation chip reads `羅馬拼音`.

- [ ] **Step 4: Verify immediate generation feedback**

Click `執行 AI 轉錄` once. Before the request completes, confirm the same row turns purple and shows
the preparation/transcription message. Confirm that a request failure restores the yellow retry row,
while a running job keeps updating its purple row.

- [ ] **Step 5: Record final evidence**

Report test counts, build result, live visual result, and any Chrome-internal reload step that still
requires the user. Do not begin the scoring redesign until all checks above pass.
