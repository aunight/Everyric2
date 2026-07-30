# Karaoke Scoring Mic Line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hard-to-see microphone particle trail with a Japanese-karaoke-style segmented line by default, retain a persisted line/dot toggle, and prevent scoring from producing false zeroes before the microphone is active.

**Architecture:** Keep microphone capture and score thresholds unchanged. Add two small pure helpers: one groups adjacent pitch samples into safe render segments, and one normalizes the scoring settings patch so enabling scoring also enables microphone pitch. `PipController` owns presentation and the footer toggle; `content.ts` owns persistence and reports `null` while capture is inactive.

**Tech Stack:** TypeScript, Canvas 2D, Chrome Extension Manifest V3, Node test runner, Vite.

---

## File Map

- Create `everyric2-chrome/src/lib/mic-trace.ts`: pure line-segmentation rules.
- Create `everyric2-chrome/src/lib/scoring-settings.ts`: pure one-switch settings normalization.
- Create `everyric2-chrome/tests/scoring-ui.test.mjs`: behavioral and wiring regression coverage.
- Modify `everyric2-chrome/src/types.ts`: persisted `micDisplayMode` contract.
- Modify `everyric2-chrome/src/lib/settings.ts`: default line mode.
- Modify `everyric2-chrome/src/lib/mic-pitch.ts`: distinguish capture-active from permission-starting.
- Modify `everyric2-chrome/src/ui/pip.ts`: toggle, nullable mic state, line/dot drawing.
- Modify `everyric2-chrome/src/content.ts`: one-switch scoring, persistence, PiP wiring.
- Modify `everyric2-chrome/_locales/{zh_TW,en,ja,ko}/messages.json`: toggle tooltip.

### Task 1: Segment microphone pitch into honest line strokes

**Files:**
- Create: `everyric2-chrome/src/lib/mic-trace.ts`
- Create: `everyric2-chrome/tests/scoring-ui.test.mjs`

- [ ] **Step 1: Write the failing behavior tests**

```js
import { buildMicTraceSegments } from '../src/lib/mic-trace.ts';

test('continuous mic samples become adjacent line segments', () => {
  const points = [
    { t: 1, midi: 60, judgement: 'hit' },
    { t: 1.05, midi: 60.2, judgement: 'hit' },
    { t: 1.1, midi: 60.1, judgement: 'near' },
  ];
  assert.deepEqual(buildMicTraceSegments(points), [
    { from: points[0], to: points[1], judgement: 'hit' },
    { from: points[1], to: points[2], judgement: 'near' },
  ]);
});

test('breaths and implausible pitch jumps break the line', () => {
  const points = [
    { t: 1, midi: 60, judgement: null },
    { t: 1.3, midi: 60, judgement: null },
    { t: 1.35, midi: 66, judgement: 'miss' },
  ];
  assert.deepEqual(buildMicTraceSegments(points), []);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
node --experimental-strip-types --test tests/scoring-ui.test.mjs
```

Expected: FAIL because `src/lib/mic-trace.ts` does not exist.

- [ ] **Step 3: Implement the minimal pure helper**

```ts
import type { Judgement } from './karaoke-score';

export interface MicTracePoint {
  t: number;
  midi: number;
  judgement: Judgement | null;
}

export interface MicTraceSegment {
  from: MicTracePoint;
  to: MicTracePoint;
  judgement: Judgement | null;
}

const MAX_GAP_SEC = 0.16;
const MAX_JUMP_ST = 4;

export function buildMicTraceSegments(points: MicTracePoint[]): MicTraceSegment[] {
  const segments: MicTraceSegment[] = [];
  for (let i = 1; i < points.length; i++) {
    const from = points[i - 1];
    const to = points[i];
    if (to.t - from.t <= MAX_GAP_SEC && Math.abs(to.midi - from.midi) <= MAX_JUMP_ST) {
      segments.push({ from, to, judgement: to.judgement });
    }
  }
  return segments;
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same command. Expected: 2 tests pass.

### Task 2: Make scoring a one-switch feature without false zeroes

**Files:**
- Create: `everyric2-chrome/src/lib/scoring-settings.ts`
- Modify: `everyric2-chrome/src/lib/mic-pitch.ts`
- Modify: `everyric2-chrome/src/content.ts:789-930`
- Test: `everyric2-chrome/tests/scoring-ui.test.mjs`

- [ ] **Step 1: Add failing tests for normalized settings**

```js
import { normalizeScoringSettingsPatch } from '../src/lib/scoring-settings.ts';

test('enabling scoring also enables microphone pitch', () => {
  assert.deepEqual(normalizeScoringSettingsPatch({ karaokeScoring: true }), {
    karaokeScoring: true,
    micPitch: true,
  });
});

test('disabling scoring does not turn off an independently enabled mic', () => {
  assert.deepEqual(normalizeScoringSettingsPatch({ karaokeScoring: false }), {
    karaokeScoring: false,
  });
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Expected: FAIL because `normalizeScoringSettingsPatch` is missing.

- [ ] **Step 3: Add the pure normalization helper**

```ts
import type { Settings } from '../types';

export function normalizeScoringSettingsPatch(
  patch: Partial<Settings>,
): Partial<Settings> {
  return patch.karaokeScoring === true ? { ...patch, micPitch: true } : patch;
}
```

- [ ] **Step 4: Wire the normalized patch and active-capture state**

At the top of `handleSettingsChange`:

```ts
patch = normalizeScoringSettingsPatch(patch);
settings = await saveSettings(patch);
```

In `MicPitch`:

```ts
isCapturing(): boolean {
  return this.timer !== undefined;
}
```

In PiP options:

```ts
getMicSamples: () => micPitch.isCapturing() ? micPitch.samples() : null,
```

Only call `scoreTracker.advance(now)` when the returned value is not `null`.

- [ ] **Step 5: Run the focused tests and typecheck**

```bash
node --experimental-strip-types --test tests/scoring-ui.test.mjs
npm run typecheck
```

Expected: tests pass and TypeScript exits 0.

### Task 3: Add the persisted line/dot mode and PiP control

**Files:**
- Modify: `everyric2-chrome/src/types.ts:480-490`
- Modify: `everyric2-chrome/src/lib/settings.ts:24-34`
- Modify: `everyric2-chrome/src/ui/pip.ts:35-115,360-460,600-810,1040-1100,1432-1460,1995-2045`
- Modify: `everyric2-chrome/src/content.ts:850-910,3090-3190`
- Modify: `everyric2-chrome/_locales/{zh_TW,en,ja,ko}/messages.json`
- Test: `everyric2-chrome/tests/scoring-ui.test.mjs`

- [ ] **Step 1: Add failing contract and wiring assertions**

```js
assert.match(typesSource, /micDisplayMode:\s*'line'\s*\|\s*'dots'/);
assert.match(settingsSource, /micDisplayMode:\s*'line'/);
assert.match(pipSource, /buildMicTraceSegments/);
assert.match(pipSource, /onMicDisplayModeChange/);
assert.match(pipSource, /micDisplayMode === 'line'/);
```

Also parse every locale and assert `pip_controls_micDisplayToggle.message` is present.

- [ ] **Step 2: Run the focused test and verify RED**

Expected: FAIL at the first missing setting assertion.

- [ ] **Step 3: Add the setting and PiP option contract**

```ts
// Settings
micDisplayMode: 'line' | 'dots';

// DEFAULT_SETTINGS
micDisplayMode: 'line',

// PipOptions
micDisplayMode: 'line' | 'dots';
onMicDisplayModeChange: (mode: 'line' | 'dots') => void;
getMicSamples: (() => MicSample[] | null) | null;
```

- [ ] **Step 4: Add the footer toggle**

Create `micDisplayBtn` using existing `ey-pip-play ey-pip-metro-opt` styles. Its label is `━` in line
mode and `••` in dots mode. Clicking changes only presentation, calls
`onMicDisplayModeChange(next)`, and does not reset `ScoreTracker`.

- [ ] **Step 5: Draw the two modes**

First map visible mic samples to `MicTracePoint[]` after playback-rate and octave correction. Feed
each sample into `ScoreTracker` exactly once as today.

For line mode:

```ts
for (const segment of buildMicTraceSegments(points)) {
  ctx.lineCap = 'round';
  ctx.strokeStyle = 'rgba(0, 0, 0, 0.42)';
  ctx.lineWidth = 8;
  stroke(segment);
  ctx.strokeStyle = judgementColor(segment.judgement);
  ctx.lineWidth = 5;
  stroke(segment);
}
```

For dots mode, preserve the current `arc(..., 2.2)` path. In both modes, draw the newest point with
a small bright circular head so the current detected pitch remains obvious.

- [ ] **Step 6: Persist mode changes from content**

Pass the initial setting and callback:

```ts
micDisplayMode: settings.micDisplayMode,
onMicDisplayModeChange: mode => void handleSettingsChange({ micDisplayMode: mode }),
```

When a settings patch contains `micDisplayMode`, call `pip.setMicDisplayMode(settings.micDisplayMode)`.

- [ ] **Step 7: Add locale tooltips**

Use:

- zh_TW: `切換麥克風音高顯示：橫線／點狀`
- en: `Switch microphone pitch display: line / dots`
- ja: `マイク音程表示を切り替え：ライン／ドット`
- ko: `마이크 음정 표시 전환: 선 / 점`

- [ ] **Step 8: Run focused tests and typecheck**

Expected: all focused tests pass and TypeScript exits 0.

### Task 4: Regression and visual verification

**Files:**
- Verify all files above without unrelated rewrites.

- [ ] **Step 1: Run the existing score-engine check**

```bash
node --experimental-strip-types scripts/score-check.mjs
```

Expected: `score-check ok`.

- [ ] **Step 2: Run the full Chrome test suite**

```bash
npm test
```

Expected: 0 failures.

- [ ] **Step 3: Build production assets**

```bash
npm run build
```

Expected: TypeScript and Vite exit 0.

- [ ] **Step 4: Check patch hygiene**

```bash
git diff --check -- everyric2-chrome docs/superpowers
```

Expected: no output.

- [ ] **Step 5: Inspect the actual PiP lane**

Reload the unpacked extension, open a synchronized song with pitch notes, enable scoring, and verify:

1. Microphone permission is requested without separately enabling mic pitch.
2. The default trail is a thick segmented line with a dark halo.
3. Breaths create gaps and large pitch jumps are not connected.
4. Hit/near/miss colors remain green/yellow/red.
5. The footer `━` / `••` button switches modes without resetting the current score.
6. Denied microphone permission shows the no-mic HUD and does not accumulate a score.
