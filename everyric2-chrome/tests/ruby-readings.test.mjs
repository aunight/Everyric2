import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import * as karaoke from '../src/ui/karaoke.ts';

const overlaySource = readFileSync(new URL('../src/ui/overlay.ts', import.meta.url), 'utf8');
const pipSource = readFileSync(new URL('../src/ui/pip.ts', import.meta.url), 'utf8');
const karaokeSource = readFileSync(new URL('../src/ui/karaoke.ts', import.meta.url), 'utf8');
const css = readFileSync(new URL('../public/overlay.css', import.meta.url), 'utf8');

test('maps timed Hiragana only to the kanji word with greatest overlap', () => {
  assert.equal(typeof karaoke.buildKanjiRubyReadings, 'function');

  const words = [
    { word: '未', start: 1, end: 1.5 },
    { word: '来', start: 1.5, end: 2 },
    { word: 'は', start: 2, end: 2.3 },
    { word: '変', start: 2.3, end: 3 },
    { word: 'わ', start: 3, end: 3.3 },
    { word: 'る', start: 3.3, end: 3.6 },
  ];
  const line = { text: '未来は変わる', words };
  const readings = karaoke.buildKanjiRubyReadings(line, [
    { text: 'み', start: 1, end: 1.5 },
    { text: 'ら', start: 1.5, end: 1.75 },
    { text: 'い', start: 1.75, end: 2 },
    { text: 'わ', start: 2, end: 2.3 },
    { text: 'か', start: 2.3, end: 2.65 },
    { text: 'わ', start: 2.65, end: 3 },
    { text: 'る', start: 3.3, end: 3.6 },
  ]);

  assert.equal(readings.get(words[0]), 'み');
  assert.equal(readings.get(words[1]), 'らい');
  assert.equal(readings.get(words[2]), undefined);
  assert.equal(readings.get(words[3]), 'かわ');
  assert.equal(readings.get(words[4]), undefined);
});

test('does not guess furigana when original word timing is unavailable', () => {
  assert.equal(typeof karaoke.buildKanjiRubyReadings, 'function');
  assert.equal(
    karaoke.buildKanjiRubyReadings(
      { text: '未来' },
      [{ text: 'みらい', start: 1, end: 2 }],
    ).size,
    0,
  );
});

test('normal lyrics and PiP share semantic ruby rendering with a standalone fallback', () => {
  assert.match(karaokeSource, /document\.createElement\('ruby'\)/);
  assert.match(karaokeSource, /document\.createElement\('rt'\)/);
  assert.match(overlaySource, /buildKanjiRubyReadings/);
  assert.match(overlaySource, /appendRubyText/);
  assert.match(pipSource, /buildKanjiRubyReadings/);
  assert.match(pipSource, /appendRubyText/);
  assert.match(css, /\.ey-ruby\s+rt\s*\{/);
  assert.match(css, /\.ey-hide-pron\s+\.ey-ruby\s+rt/);
});
