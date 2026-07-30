import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const typesSource = readFileSync(new URL('../src/types.ts', import.meta.url), 'utf8');
const settingsSource = readFileSync(new URL('../src/lib/settings.ts', import.meta.url), 'utf8');
const micPitchSource = readFileSync(new URL('../src/lib/mic-pitch.ts', import.meta.url), 'utf8');
const pipSource = readFileSync(new URL('../src/ui/pip.ts', import.meta.url), 'utf8');
const contentSource = readFileSync(new URL('../src/content.ts', import.meta.url), 'utf8');
const overlaySource = readFileSync(new URL('../src/ui/overlay.ts', import.meta.url), 'utf8');

async function optionalImport(path) {
  try {
    return await import(path);
  } catch {
    return {};
  }
}

test('continuous mic samples become adjacent line segments', async () => {
  const { buildMicTraceSegments } = await optionalImport('../src/lib/mic-trace.ts');
  assert.equal(typeof buildMicTraceSegments, 'function');
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

test('breaths and implausible pitch jumps break the line', async () => {
  const { buildMicTraceSegments } = await optionalImport('../src/lib/mic-trace.ts');
  assert.equal(typeof buildMicTraceSegments, 'function');
  assert.deepEqual(buildMicTraceSegments([
    { t: 1, midi: 60, judgement: null },
    { t: 1.3, midi: 60, judgement: null },
    { t: 1.35, midi: 66, judgement: 'miss' },
  ]), []);
});

test('judged microphone points become clipped target-note hit segments', async () => {
  const { buildNoteHitSegments } = await optionalImport('../src/ui/note-hit.ts');
  assert.equal(typeof buildNoteHitSegments, 'function');
  const notes = [{ midi: 60, start: 1, end: 1.5 }];
  const points = [
    { t: 1, midi: 60, judgement: 'hit' },
    { t: 1.05, midi: 60.1, judgement: 'hit' },
    { t: 1.1, midi: 61.1, judgement: 'near' },
  ];

  assert.deepEqual(buildNoteHitSegments(points, notes), [
    { noteIndex: 0, midi: 60, start: 1, end: 1.1, judgement: 'hit' },
    { noteIndex: 0, midi: 60, start: 1.1, end: 1.18, judgement: 'near' },
  ]);
});

test('points outside target notes never paint note bars', async () => {
  const { buildNoteHitSegments } = await optionalImport('../src/ui/note-hit.ts');
  assert.equal(typeof buildNoteHitSegments, 'function');
  assert.deepEqual(buildNoteHitSegments(
    [{ t: 3, midi: 60, judgement: null }],
    [{ midi: 60, start: 1, end: 2 }],
  ), []);
});

test('enabling scoring also enables microphone pitch', async () => {
  const { normalizeScoringSettings, normalizeScoringSettingsPatch } =
    await optionalImport('../src/lib/scoring-settings.ts');
  assert.equal(typeof normalizeScoringSettingsPatch, 'function');
  assert.equal(typeof normalizeScoringSettings, 'function');
  assert.deepEqual(normalizeScoringSettingsPatch({ karaokeScoring: true }), {
    karaokeScoring: true,
    micPitch: true,
  });
  assert.deepEqual(normalizeScoringSettingsPatch({ karaokeScoring: false }), {
    karaokeScoring: false,
  });
  assert.deepEqual(normalizeScoringSettings({
    karaokeScoring: true,
    micPitch: false,
    micDisplayMode: 'dots',
    marker: 'legacy',
  }), {
    karaokeScoring: true,
    micPitch: true,
    micDisplayMode: 'trace',
    marker: 'legacy',
  });
});

test('legacy microphone display modes migrate to trace and new installs use notes', async () => {
  const { normalizeMicDisplayMode } =
    await optionalImport('../src/lib/scoring-settings.ts');

  assert.equal(typeof normalizeMicDisplayMode, 'function');
  assert.equal(normalizeMicDisplayMode('line'), 'trace');
  assert.equal(normalizeMicDisplayMode('dots'), 'trace');
  assert.equal(normalizeMicDisplayMode('trace'), 'trace');
  assert.equal(normalizeMicDisplayMode('notes'), 'notes');
  assert.equal(normalizeMicDisplayMode('unexpected'), 'notes');
  assert.match(typesSource, /type MicDisplayMode = 'trace' \| 'notes'/);
  assert.match(typesSource, /micDisplayMode:\s*MicDisplayMode/);
  assert.match(settingsSource, /micDisplayMode:\s*'notes'/);
});

test('trace and note-hit display modes are wired through PiP and settings', () => {
  assert.match(pipSource, /buildMicTraceSegments/);
  assert.match(pipSource, /buildNoteHitSegments/);
  assert.match(pipSource, /this\.micDisplayMode === 'trace'/);
  assert.match(pipSource, /'#ffd54f'/);
  assert.match(
    pipSource,
    /textContent = this\.micDisplayMode === 'trace' \? '線' : '音'/,
  );
  assert.match(overlaySource, /overlay\.settings\.micDisplayMode\.trace/);
  assert.match(overlaySource, /overlay\.settings\.micDisplayMode\.notes/);
  assert.match(contentSource, /micDisplayMode:\s*settings\.micDisplayMode/);
  assert.match(contentSource, /handleSettingsChange\(\{\s*micDisplayMode:\s*mode\s*\}\)/);
});

test('inactive microphone is distinguishable from active silence', () => {
  assert.match(micPitchSource, /isCapturing\(\):\s*boolean/);
  assert.match(contentSource, /micPitch\.isCapturing\(\)\s*\?\s*micPitch\.samples\(\)\s*:\s*null/);
  assert.match(pipSource, /if\s*\(this\.scoring\s*&&\s*mic\s*!==\s*null\)/);
});

test('all locales explain both scoring display modes', () => {
  for (const locale of ['zh_TW', 'en', 'ja', 'ko']) {
    const messages = JSON.parse(readFileSync(
      new URL(`../_locales/${locale}/messages.json`, import.meta.url),
      'utf8',
    ));
    for (const key of [
      'pip_controls_micDisplayToggle',
      'overlay_settings_row_micDisplayMode',
      'overlay_settings_micDisplayMode_trace',
      'overlay_settings_micDisplayMode_notes',
    ]) {
      assert.ok(messages[key]?.message, `${locale} is missing ${key}`);
    }
  }
});

test('Traditional Chinese names the Japanese karaoke style without a space', () => {
  const messages = JSON.parse(readFileSync(
    new URL('../_locales/zh_TW/messages.json', import.meta.url),
    'utf8',
  ));
  const localeGenerator = readFileSync(
    new URL('../../scripts/gen_zh_locale.py', import.meta.url),
    'utf8',
  );

  assert.equal(messages.overlay_settings_micDisplayMode_notes.message, '命中音符（日K）');
  assert.match(
    localeGenerator,
    /"overlay_settings_micDisplayMode_notes": "命中音符（日K）"/,
  );
});
