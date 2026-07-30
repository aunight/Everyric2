# Karaoke Scoring Display Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore original lyric labels on pitch notes and replace the line/dot microphone display with selectable trace and DAM-style note-hit scoring modes.

**Architecture:** Keep scoring decisions in `ScoreTracker`, add a pure helper that converts judged microphone samples into clipped target-note overlays, and keep original lyrics separate from optional pronunciation on each `PitchNote`. Normalize persisted display values at the settings boundary, then wire the same `trace | notes` value through the settings panel, content script, and PiP quick switch.

**Tech Stack:** TypeScript, Canvas 2D, Chrome extension storage, Node.js test runner, Vite.

**Dirty-worktree constraint:** The Chrome files already contain unrelated user changes. Do not stage or commit shared production files during execution. Each task ends with focused tests and `git diff --check`; the already-committed design and plan documents provide checkpoints without absorbing unrelated work.

---

## File map

- Modify `everyric2-chrome/src/types.ts`: define the persisted `MicDisplayMode`.
- Modify `everyric2-chrome/src/lib/scoring-settings.ts`: normalize legacy display modes and the scoring/microphone invariant.
- Modify `everyric2-chrome/src/lib/settings.ts`: use `notes` as the default mode.
- Modify `everyric2-chrome/src/ui/pitch-labels.ts`: attach original lyrics independently from pronunciation.
- Create `everyric2-chrome/src/ui/note-hit.ts`: pure judged-sample to target-note overlay conversion.
- Modify `everyric2-chrome/src/ui/pip.ts`: collect visual scoring history, render either trace or note overlays, and show `線`/`音`.
- Modify `everyric2-chrome/src/ui/overlay.ts`: add the scoring display select.
- Modify `everyric2-chrome/src/content.ts`: keep the normalized mode live in PiP.
- Modify four `everyric2-chrome/_locales/*/messages.json` catalogs: mode labels and tooltips.
- Modify `everyric2-chrome/tests/scoring-ui.test.mjs`: settings migration, hit segments, and UI wiring.
- Modify `everyric2-chrome/tests/pitch-ui.test.mjs`: original lyric/pronunciation separation.

### Task 1: Normalize the persisted display mode

**Files:**
- Modify: `everyric2-chrome/tests/scoring-ui.test.mjs`
- Modify: `everyric2-chrome/src/types.ts`
- Modify: `everyric2-chrome/src/lib/scoring-settings.ts`
- Modify: `everyric2-chrome/src/lib/settings.ts`

- [ ] **Step 1: Write the failing settings tests**

Replace the old line/dot source assertion with real normalization assertions:

```js
test('legacy microphone display modes migrate to trace and new installs use notes', async () => {
  const { normalizeMicDisplayMode } =
    await import('../src/lib/scoring-settings.ts');

  assert.equal(normalizeMicDisplayMode('line'), 'trace');
  assert.equal(normalizeMicDisplayMode('dots'), 'trace');
  assert.equal(normalizeMicDisplayMode('trace'), 'trace');
  assert.equal(normalizeMicDisplayMode('notes'), 'notes');
  assert.equal(normalizeMicDisplayMode('unexpected'), 'notes');
  assert.match(typesSource, /micDisplayMode:\s*MicDisplayMode/);
  assert.match(typesSource, /type MicDisplayMode = 'trace' \| 'notes'/);
  assert.match(settingsSource, /micDisplayMode:\s*'notes'/);
});
```

Extend the existing legacy full-settings test so the input contains
`micDisplayMode: 'dots'` and the output preserves its marker while returning
`micDisplayMode: 'trace'`.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test \
  --test-name-pattern="legacy microphone display modes" \
  tests/scoring-ui.test.mjs
```

Expected: FAIL because `normalizeMicDisplayMode` and `MicDisplayMode` do not exist and the default is still `line`.

- [ ] **Step 3: Add the type and normalization**

In `src/types.ts`:

```ts
export type MicDisplayMode = 'trace' | 'notes';

export interface Settings {
  // ...
  micDisplayMode: MicDisplayMode;
}
```

In `src/lib/scoring-settings.ts`:

```ts
import type { MicDisplayMode, Settings } from '../types';

export function normalizeMicDisplayMode(value: unknown): MicDisplayMode {
  if (value === 'trace' || value === 'line' || value === 'dots') return 'trace';
  return 'notes';
}

export function normalizeScoringSettings<
  T extends {
    karaokeScoring: boolean;
    micPitch: boolean;
    micDisplayMode?: unknown;
  },
>(settings: T): Omit<T, 'micDisplayMode'> & { micDisplayMode: MicDisplayMode } {
  return {
    ...settings,
    micPitch: settings.karaokeScoring ? true : settings.micPitch,
    micDisplayMode: normalizeMicDisplayMode(settings.micDisplayMode),
  };
}
```

Keep `normalizeScoringSettingsPatch` unchanged: enabling scoring still enables microphone input.

In `src/lib/settings.ts`, set:

```ts
micDisplayMode: 'notes',
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same focused command. Expected: PASS.

- [ ] **Step 5: Check the focused diff**

Run:

```bash
git diff --check -- \
  everyric2-chrome/src/types.ts \
  everyric2-chrome/src/lib/scoring-settings.ts \
  everyric2-chrome/src/lib/settings.ts \
  everyric2-chrome/tests/scoring-ui.test.mjs
```

Expected: no output.

### Task 2: Separate original lyric labels from pronunciation

**Files:**
- Modify: `everyric2-chrome/tests/pitch-ui.test.mjs`
- Modify: `everyric2-chrome/src/ui/pitch-labels.ts`
- Modify: `everyric2-chrome/src/ui/pip.ts`

- [ ] **Step 1: Write the failing label tests**

Update the Chinese expectations from `note.pron` to `note.lyric`, assert that polluted Japanese readings are not used, and add a non-Chinese case:

```js
test('original note lyrics survive when pronunciation is absent', () => {
  const notes = [
    { start: 1, end: 1.4 },
    { start: 1.4, end: 1.8 },
  ];
  const line = {
    text: 'hello',
    words: [
      { word: 'hel', start: 1, end: 1.4 },
      { word: 'lo', start: 1.4, end: 1.8 },
    ],
  };

  attachPitchNoteLabels(line, notes, 'romaji', 'en');

  assert.deepEqual(notes.map(note => note.lyric), ['hel', 'lo']);
  assert.deepEqual(notes.map(note => note.pron), [undefined, undefined]);
});
```

Add source checks that `PitchNote` contains both `lyric?: string` and `pron?: string`, and that lyric drawing is outside the `noteAttach` condition.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test tests/pitch-ui.test.mjs
```

Expected: FAIL because labels are still stored in `pron`.

- [ ] **Step 3: Implement independent fields**

Change `PitchLabelNote` to:

```ts
export interface PitchLabelNote {
  start: number;
  end: number;
  lyric?: string;
  pron?: string;
}
```

Generalize the overlap/nearest helpers to append to either `lyric` or `pron`. In
`attachPitchNoteLabels`:

1. Always map `line.words[].word` to `lyric`.
2. Return after lyric mapping for Chinese, leaving `pron` empty.
3. For other languages, map `resolvedPronSegments` to `pron`.

Change `PitchNote` in `pip.ts` to include both fields. Draw `n.lyric` below the target note regardless of `pitchPronPosition`. Draw `n.pron` on the next baseline only when `noteAttach` is true. Use original text colors, and do not gate lyric drawing on note width.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same focused command. Expected: all `pitch-ui` tests pass.

- [ ] **Step 5: Check the focused diff**

Run:

```bash
git diff --check -- \
  everyric2-chrome/src/ui/pitch-labels.ts \
  everyric2-chrome/src/ui/pip.ts \
  everyric2-chrome/tests/pitch-ui.test.mjs
```

Expected: no output.

### Task 3: Convert judged microphone samples into note-hit overlays

**Files:**
- Create: `everyric2-chrome/src/ui/note-hit.ts`
- Modify: `everyric2-chrome/tests/scoring-ui.test.mjs`

- [ ] **Step 1: Write the failing pure-logic tests**

Add tests for clipped, merged, and ignored points:

```js
test('judged microphone points become clipped target-note hit segments', async () => {
  const { buildNoteHitSegments } = await import('../src/ui/note-hit.ts');
  const notes = [{ midi: 60, start: 1, end: 1.5 }];
  const points = [
    { t: 1.00, midi: 60, judgement: 'hit' },
    { t: 1.05, midi: 60.1, judgement: 'hit' },
    { t: 1.10, midi: 61.1, judgement: 'near' },
  ];

  assert.deepEqual(buildNoteHitSegments(points, notes), [
    { noteIndex: 0, midi: 60, start: 1, end: 1.10, judgement: 'hit' },
    { noteIndex: 0, midi: 60, start: 1.10, end: 1.18, judgement: 'near' },
  ]);
});

test('points outside target notes never paint note bars', async () => {
  const { buildNoteHitSegments } = await import('../src/ui/note-hit.ts');
  assert.deepEqual(buildNoteHitSegments(
    [{ t: 3, midi: 60, judgement: null }],
    [{ midi: 60, start: 1, end: 2 }],
  ), []);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test \
  --test-name-pattern="target-note|outside target" \
  tests/scoring-ui.test.mjs
```

Expected: FAIL because `src/ui/note-hit.ts` does not exist.

- [ ] **Step 3: Implement the pure helper**

Create types:

```ts
export interface NoteHitPoint extends MicTracePoint {}

export interface NoteHitSegment {
  noteIndex: number;
  midi: number;
  start: number;
  end: number;
  judgement: Judgement;
}
```

`buildNoteHitSegments(points, notes)` must:

1. Skip `judgement === null`.
2. Find a note within the same 150 ms grace window used by scoring, choosing the smallest octave-folded pitch error when candidates overlap.
3. Clamp each painted interval to the target note.
4. Use the next nearby sample time as the interval end, with an 80 ms fallback.
5. Merge adjacent intervals only when note index and judgement match.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same focused command. Expected: PASS.

- [ ] **Step 5: Check the new helper**

Run:

```bash
git diff --check -- \
  everyric2-chrome/src/ui/note-hit.ts \
  everyric2-chrome/tests/scoring-ui.test.mjs
```

Expected: no output.

### Task 4: Wire the two rendering modes through PiP and settings

**Files:**
- Modify: `everyric2-chrome/tests/scoring-ui.test.mjs`
- Modify: `everyric2-chrome/src/ui/pip.ts`
- Modify: `everyric2-chrome/src/ui/overlay.ts`
- Modify: `everyric2-chrome/src/content.ts`
- Modify: `everyric2-chrome/_locales/zh_TW/messages.json`
- Modify: `everyric2-chrome/_locales/en/messages.json`
- Modify: `everyric2-chrome/_locales/ja/messages.json`
- Modify: `everyric2-chrome/_locales/ko/messages.json`

- [ ] **Step 1: Write failing UI-wiring tests**

Assert:

```js
assert.match(pipSource, /this\.micDisplayMode === 'trace'/);
assert.match(pipSource, /buildNoteHitSegments/);
assert.match(pipSource, /'#ffd54f'/);
assert.match(pipSource, /textContent = this\.micDisplayMode === 'trace' \? '線' : '音'/);
assert.match(overlaySource, /overlay\.settings\.micDisplayMode\.trace/);
assert.match(overlaySource, /overlay\.settings\.micDisplayMode\.notes/);
assert.match(contentSource, /micDisplayMode:\s*settings\.micDisplayMode/);
```

For every locale, require:

- `pip_controls_micDisplayToggle`
- `overlay_settings_row_micDisplayMode`
- `overlay_settings_micDisplayMode_trace`
- `overlay_settings_micDisplayMode_notes`

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd everyric2-chrome
node --experimental-strip-types --test \
  --test-name-pattern="display modes|locales" \
  tests/scoring-ui.test.mjs
```

Expected: FAIL because the PiP still accepts `line | dots` and the settings select/locales are absent.

- [ ] **Step 3: Update PiP state and button**

Import `MicDisplayMode` in `pip.ts`. Change `PipOptions`, callback, field, setter, and content wiring to use it. The button must switch:

```ts
const next: MicDisplayMode = this.micDisplayMode === 'trace' ? 'notes' : 'trace';
```

and show:

```ts
this.micDisplayBtn.textContent = this.micDisplayMode === 'trace' ? '線' : '音';
```

Switching calls `renderPitch` only; do not reset `ScoreTracker`.

- [ ] **Step 4: Keep scoring visual history and render the selected mode**

In `PipController`, add a bounded current-song history:

```ts
private scoreVisualPoints: MicTracePoint[] = [];
private lastScoreVisualAt = -1;
```

When a new microphone sample receives a judgement, append it only if `s.at` is newer than
`lastScoreVisualAt`; cap the array at 12,000 points. Clear it when the melody signature changes,
when scoring is newly enabled, and when the score is flushed for a song change or disable.

Move microphone judging before the target-note draw pass. In `notes` mode:

1. Build visible overlays with `buildNoteHitSegments`.
2. Draw each overlay on the target note's y-coordinate and clipped time range.
3. Use hit `#ffd54f`, near `#ffca28`, miss `#ef5350`.
4. Draw the original lyric and optional pronunciation after the overlay so text remains legible.

In `trace` mode, keep `buildMicTraceSegments` and the existing halo, but remove the dot branch.

- [ ] **Step 5: Add the settings select and translations**

In `overlay.ts`:

```ts
const micDisplayMode = this.buildSelect(
  [
    ['trace', t('overlay.settings.micDisplayMode.trace')],
    ['notes', t('overlay.settings.micDisplayMode.notes')],
  ],
  this.settings.micDisplayMode,
  value => this.callbacks.onSettingsChange({
    micDisplayMode: value as Settings['micDisplayMode'],
  }),
);
```

Place it beside the karaoke scoring setting. Add natural labels in Traditional Chinese, English,
Japanese, and Korean. The Traditional Chinese strings are:

```json
"pip_controls_micDisplayToggle": {
  "message": "切換採點顯示：線條軌跡／命中音符"
},
"overlay_settings_row_micDisplayMode": {
  "message": "採點顯示方式"
},
"overlay_settings_micDisplayMode_trace": {
  "message": "線條軌跡"
},
"overlay_settings_micDisplayMode_notes": {
  "message": "命中音符（日 K）"
}
```

- [ ] **Step 6: Run focused tests and typecheck**

Run:

```bash
cd everyric2-chrome
npm test -- --test-name-pattern="display modes|locales|target-note|original note lyrics"
npm run typecheck
```

Expected: all selected tests pass and TypeScript exits 0.

- [ ] **Step 7: Check the focused diff**

Run:

```bash
git diff --check -- \
  everyric2-chrome/src \
  everyric2-chrome/_locales \
  everyric2-chrome/tests
```

Expected: no output.

### Task 5: Full verification

**Files:**
- Verify all changed Chrome files.

- [ ] **Step 1: Run all Chrome tests**

```bash
cd everyric2-chrome
npm test
```

Expected: zero failures.

- [ ] **Step 2: Run the scoring engine self-check**

```bash
cd everyric2-chrome
node --experimental-strip-types scripts/score-check.mjs
```

Expected: `score-check ok`.

- [ ] **Step 3: Run a production build**

```bash
cd everyric2-chrome
npm run build
```

Expected: TypeScript and Vite exit 0. The existing large-chunk advisory is allowed.

- [ ] **Step 4: Parse every locale catalog**

```bash
cd everyric2-chrome
node -e "for (const l of ['zh_TW','en','ja','ko']) JSON.parse(require('fs').readFileSync('_locales/'+l+'/messages.json','utf8')); console.log('locales ok')"
```

Expected: `locales ok`.

- [ ] **Step 5: Review requirements and diff**

Confirm from the diff:

- `trace | notes` is the only live mode type.
- Legacy `line | dots` values are normalized.
- Original lyric drawing is independent of pronunciation placement.
- Note mode paints judged target-note subranges.
- Trace mode still renders the microphone path.
- Switching modes does not reset scoring.
- No unrelated dirty files were staged.

Run:

```bash
git diff --check -- everyric2-chrome docs/superpowers
git status --short
```

Expected: no whitespace errors; pre-existing unrelated dirty files remain untouched and unstaged.
