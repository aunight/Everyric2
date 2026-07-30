# Detected Microphone Pitch Bars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Chrome extension’s `命中音符（日K）` mode draw short bars at the microphone’s detected pitch instead of recoloring the original target notes.

**Architecture:** Replace the target-note clipping helper with a pure detected-pitch bar builder that preserves microphone MIDI and only groups adjacent samples for visual stability. Keep scoring and persisted mode values unchanged; update the PiP renderer so target notes remain gray while separately rendered microphone bars use judgement colors.

**Tech Stack:** TypeScript, Canvas 2D, Node test runner, esbuild/Vite Chrome-extension build

---

## File Structure

- Create `everyric2-chrome/src/ui/detected-pitch-bars.ts`: pure conversion from scored microphone samples to quantized, contiguous pitch bars.
- Delete `everyric2-chrome/src/ui/note-hit.ts`: remove the obsolete target-note clipping implementation.
- Modify `everyric2-chrome/src/ui/pip.ts`: retain all reliable scoring samples and render actual microphone bars by their MIDI.
- Modify `everyric2-chrome/tests/scoring-ui.test.mjs`: replace target-note recoloring expectations with actual-pitch behavior and renderer wiring assertions.

### Task 1: Replace Target-Note Clipping With Actual-Pitch Segmentation

**Files:**
- Create: `everyric2-chrome/src/ui/detected-pitch-bars.ts`
- Delete: `everyric2-chrome/src/ui/note-hit.ts`
- Modify: `everyric2-chrome/tests/scoring-ui.test.mjs`

- [ ] **Step 1: Replace the old helper tests with failing actual-pitch tests**

Replace the two `buildNoteHitSegments` tests with:

```js
test('detected pitch bars preserve microphone midi instead of target midi', async () => {
  const { buildDetectedPitchBars } =
    await optionalImport('../src/ui/detected-pitch-bars.ts');
  assert.equal(typeof buildDetectedPitchBars, 'function');
  const points = [
    { t: 1, midi: 61.1, judgement: 'near' },
    { t: 1.05, midi: 61.2, judgement: 'near' },
  ];

  assert.deepEqual(buildDetectedPitchBars(points), [
    { midi: 61, start: 1, end: 1.13, judgement: 'near' },
  ]);
});

test('reliable points outside target-note windows still become pitch bars', async () => {
  const { buildDetectedPitchBars } =
    await optionalImport('../src/ui/detected-pitch-bars.ts');
  assert.equal(typeof buildDetectedPitchBars, 'function');
  assert.deepEqual(buildDetectedPitchBars([
    { t: 3, midi: 64.2, judgement: null },
  ]), [
    { midi: 64, start: 3, end: 3.08, judgement: null },
  ]);
});

test('pitch, judgement, and timing gaps split detected pitch bars', async () => {
  const { buildDetectedPitchBars } =
    await optionalImport('../src/ui/detected-pitch-bars.ts');
  assert.equal(typeof buildDetectedPitchBars, 'function');
  assert.deepEqual(buildDetectedPitchBars([
    { t: 1, midi: 60.1, judgement: 'hit' },
    { t: 1.05, midi: 61.1, judgement: 'hit' },
    { t: 1.1, midi: 61.2, judgement: 'miss' },
    { t: 1.4, midi: 61.2, judgement: 'miss' },
  ]), [
    { midi: 60, start: 1, end: 1.05, judgement: 'hit' },
    { midi: 61, start: 1.05, end: 1.1, judgement: 'hit' },
    { midi: 61, start: 1.1, end: 1.18, judgement: 'miss' },
    { midi: 61, start: 1.4, end: 1.48, judgement: 'miss' },
  ]);
});
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
cd everyric2-chrome
node --test tests/scoring-ui.test.mjs
```

Expected: FAIL because `src/ui/detected-pitch-bars.ts` and `buildDetectedPitchBars` do not exist.

- [ ] **Step 3: Implement the pure detected-pitch bar builder**

Create `everyric2-chrome/src/ui/detected-pitch-bars.ts`:

```ts
import type { Judgement } from '../lib/karaoke-score';
import type { MicTracePoint } from '../lib/mic-trace';

export interface DetectedPitchBar {
  midi: number;
  start: number;
  end: number;
  judgement: Judgement | null;
}

const SAMPLE_TAIL_SEC = 0.08;
const MAX_SAMPLE_GAP_SEC = 0.16;
const MERGE_EPSILON_SEC = 0.02;

function roundedTime(value: number): number {
  return Math.round(value * 1000) / 1000;
}

export function buildDetectedPitchBars(points: MicTracePoint[]): DetectedPitchBar[] {
  const sortedPoints = [...points]
    .filter(point => Number.isFinite(point.t) && Number.isFinite(point.midi))
    .sort((a, b) => a.t - b.t);
  const bars: DetectedPitchBar[] = [];

  for (let index = 0; index < sortedPoints.length; index++) {
    const point = sortedPoints[index];
    const next = sortedPoints[index + 1];
    const nextIsContinuous =
      next !== undefined
      && next.t > point.t
      && next.t - point.t <= MAX_SAMPLE_GAP_SEC;
    const bar: DetectedPitchBar = {
      midi: Math.round(point.midi),
      start: roundedTime(point.t),
      end: roundedTime(nextIsContinuous ? next.t : point.t + SAMPLE_TAIL_SEC),
      judgement: point.judgement,
    };
    const previous = bars[bars.length - 1];
    if (
      previous
      && previous.midi === bar.midi
      && previous.judgement === bar.judgement
      && bar.start <= previous.end + MERGE_EPSILON_SEC
    ) {
      previous.end = Math.max(previous.end, bar.end);
    } else {
      bars.push(bar);
    }
  }

  return bars;
}
```

Delete `everyric2-chrome/src/ui/note-hit.ts`; no target-note lookup or octave-folded target matching remains in the visual segmentation layer.

- [ ] **Step 4: Run the focused tests and verify the pure helper passes**

Run:

```bash
cd everyric2-chrome
node --test tests/scoring-ui.test.mjs
```

Expected: the three new functional tests PASS; the old PiP wiring assertion may still FAIL until Task 2.

- [ ] **Step 5: Commit the segmentation change**

```bash
git add everyric2-chrome/src/ui/detected-pitch-bars.ts \
  everyric2-chrome/src/ui/note-hit.ts \
  everyric2-chrome/tests/scoring-ui.test.mjs
git commit -m "test(chrome): specify detected microphone pitch bars"
```

### Task 2: Render Microphone Bars Separately From Target Notes

**Files:**
- Modify: `everyric2-chrome/src/ui/pip.ts`
- Modify: `everyric2-chrome/tests/scoring-ui.test.mjs`

- [ ] **Step 1: Add failing source-wiring assertions**

Update the display-mode wiring test to assert the new renderer contract:

```js
test('trace and detected-pitch display modes are wired through PiP and settings', () => {
  assert.match(pipSource, /buildMicTraceSegments/);
  assert.match(pipSource, /buildDetectedPitchBars/);
  assert.match(pipSource, /this\.micDisplayMode === 'trace'/);
  assert.match(pipSource, /y\(displayMidi\)/);
  assert.doesNotMatch(pipSource, /hitsByNote/);
  assert.doesNotMatch(pipSource, /buildNoteHitSegments/);
  assert.match(pipSource, /'#ffd54f'/);
  assert.match(pipSource, /'#ff9800'/);
  assert.match(pipSource, /'#ef5350'/);
  assert.match(
    pipSource,
    /textContent = this\.micDisplayMode === 'trace' \? '線' : '音'/,
  );
  assert.match(overlaySource, /overlay\.settings\.micDisplayMode\.trace/);
  assert.match(overlaySource, /overlay\.settings\.micDisplayMode\.notes/);
  assert.match(contentSource, /micDisplayMode:\s*settings\.micDisplayMode/);
  assert.match(contentSource, /handleSettingsChange\(\{\s*micDisplayMode:\s*mode\s*\}\)/);
});

test('scoring visual history retains reliable points without a target judgement', () => {
  assert.match(
    pipSource,
    /if \(this\.scoring && sample\.at > this\.lastScoreVisualAt\)/,
  );
  assert.doesNotMatch(
    pipSource,
    /this\.scoring && judgement !== null && sample\.at > this\.lastScoreVisualAt/,
  );
});
```

- [ ] **Step 2: Run the focused test and verify renderer assertions fail**

Run:

```bash
cd everyric2-chrome
node --test tests/scoring-ui.test.mjs
```

Expected: FAIL because `pip.ts` still imports `buildNoteHitSegments`, creates `hitsByNote`, and drops null-judgement samples.

- [ ] **Step 3: Switch PiP imports and scoring visual history**

In `everyric2-chrome/src/ui/pip.ts`, replace:

```ts
import { buildNoteHitSegments, type NoteHitSegment } from './note-hit';
```

with:

```ts
import {
  buildDetectedPitchBars,
  type DetectedPitchBar,
} from './detected-pitch-bars';
```

Change the visual-history condition from:

```ts
if (this.scoring && judgement !== null && sample.at > this.lastScoreVisualAt) {
```

to:

```ts
if (this.scoring && sample.at > this.lastScoreVisualAt) {
```

This retains reliable microphone samples even when `ScoreTracker` has no active target note. Keep the existing 12,000-point cap and duplicate-wall-clock guard.

- [ ] **Step 4: Remove target-note hit mapping and keep target notes gray**

Delete `noteHitColor`, `visibleHitPoints`, `noteHits`, `hitsByNote`, and the per-target-note loop that paints hit segments. Keep the existing rule that playback progress does not color target notes while scoring in `notes` mode:

```ts
if (now > n.start && (!this.scoring || this.micDisplayMode === 'trace')) {
```

Build visible bars without passing target notes:

```ts
const visiblePitchPoints = this.scoreVisualPoints.filter(
  point => point.t >= t0 - 0.15 && point.t <= t0 + W + 0.15,
);
const detectedPitchBars =
  this.scoring && this.micDisplayMode === 'notes'
    ? buildDetectedPitchBars(visiblePitchPoints)
    : [];
```

- [ ] **Step 5: Draw actual-pitch bars after the target-note pass**

After the target-note loop and before the trace-mode branch, add:

```ts
if (this.micDisplayMode === 'notes' && detectedPitchBars.length > 0) {
  const pitchBarColor = (judgement: DetectedPitchBar['judgement']) =>
    judgement === 'hit' ? '#ffd54f'
    : judgement === 'near' ? '#ff9800'
    : judgement === 'miss' ? '#ef5350'
    : '#4dd0e1';

  ctx.save();
  for (const bar of detectedPitchBars) {
    let displayMidi = bar.midi;
    while (displayMidi < lo && displayMidi + 12 <= hi + 6) displayMidi += 12;
    while (displayMidi > hi && displayMidi - 12 >= lo - 6) displayMidi -= 12;
    if (displayMidi < lo - 1 || displayMidi > hi + 1) continue;

    const barX = x(bar.start);
    const barW = Math.max(3, x(bar.end) - barX);
    const barTop = y(displayMidi) - noteH / 2;
    ctx.globalAlpha = 0.92;
    ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
    ctx.beginPath();
    ctx.roundRect(barX - 1, barTop - 1, barW + 2, noteH + 2, noteR);
    ctx.fill();
    ctx.globalAlpha = 0.98;
    ctx.fillStyle = pitchBarColor(bar.judgement);
    ctx.beginPath();
    ctx.roundRect(barX, barTop, barW, noteH, Math.min(noteR, barW / 2));
    ctx.fill();
  }
  ctx.restore();
}
```

The bar’s Y position comes only from `bar.midi`; judgement changes color but never changes pitch position.

- [ ] **Step 6: Run focused tests and type checking**

Run:

```bash
cd everyric2-chrome
node --test tests/scoring-ui.test.mjs
npm run typecheck
```

Expected: all scoring UI tests PASS and TypeScript reports no errors.

- [ ] **Step 7: Commit the renderer change**

```bash
git add everyric2-chrome/src/ui/pip.ts everyric2-chrome/tests/scoring-ui.test.mjs
git commit -m "fix(chrome): draw detected microphone pitch bars"
```

### Task 3: Full Regression and Production-Build Verification

**Files:**
- Modify only if verification exposes a directly related defect.

- [ ] **Step 1: Run the full Chrome test suite**

Run:

```bash
cd everyric2-chrome
npm test
```

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run type checking and production build**

Run:

```bash
cd everyric2-chrome
npm run typecheck
npm run build
```

Expected: TypeScript exits 0 and the production extension is rebuilt in `everyric2-chrome/dist`.

- [ ] **Step 3: Run the scoring self-check**

Run:

```bash
cd everyric2-chrome
node scripts/score-check.mjs
```

Expected: the scoring self-check reports success and exits 0.

- [ ] **Step 4: Verify diff hygiene and changed-file scope**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~2
```

Expected: no whitespace errors; only the detected-pitch helper, scoring UI tests, PiP renderer, design, and plan files are changed by this feature.

- [ ] **Step 5: Review the built renderer manually**

Load `everyric2-chrome/dist` as an unpacked Chrome extension, open the PiP scoring view, select `命中音符（日K）`, and sing one target note deliberately sharp and then flat.

Expected:

- Gray target notes never change color.
- Sharp input appears on a higher pitch row.
- Flat input appears on a lower pitch row.
- Correct input appears gold at the detected pitch.
- Near input appears orange and miss input appears red.
- Silence produces no connecting bar.
- Switching to `線條軌跡` retains the score and renders the existing continuous trace.

- [ ] **Step 6: Commit any verification-only adjustments**

If no source change was required, skip this commit. If a directly related adjustment was required:

```bash
git add everyric2-chrome/src/ui/detected-pitch-bars.ts \
  everyric2-chrome/src/ui/pip.ts \
  everyric2-chrome/tests/scoring-ui.test.mjs
git commit -m "fix(chrome): stabilize detected pitch rendering"
```

Do not include RMVPE, Demucs, Dereverb, translation, lyrics, or unrelated UI changes.
