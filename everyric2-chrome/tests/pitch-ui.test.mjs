import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { attachPitchNoteLabels } from '../src/ui/pitch-labels.ts';

const pipSource = readFileSync(new URL('../src/ui/pip.ts', import.meta.url), 'utf8');
const css = readFileSync(new URL('../public/overlay.css', import.meta.url), 'utf8');
let pitchLabelSource = '';
try {
  pitchLabelSource = readFileSync(new URL('../src/ui/pitch-labels.ts', import.meta.url), 'utf8');
} catch {
  // RED phase: the focused helper does not exist yet.
}

function ruleBody(selector) {
  const match = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)]
    .find(candidate => candidate[1].split(',').some(item => item.trim() === selector));
  assert.ok(match, `missing CSS rule: ${selector}`);
  return match[2];
}

test('Chinese pitch labels use original lyric text and do not expose pronunciation cycling', () => {
  assert.match(pipSource, /detectLyricLanguage\(lines\.map\(line => line\.text\)\)/);
  assert.match(pitchLabelSource, /word\.word[\s\S]*?songLanguage === 'zh'/);
  assert.match(pipSource, /display\(this\.pronScriptBtn,\s*lane && this\.songLanguage !== 'zh'\)/);
});

test('Chinese pitch labels ignore polluted Japanese readings in saved lyric data', () => {
  const notes = [
    { start: 1, end: 1.4 },
    { start: 1.4, end: 1.8 },
  ];
  const line = {
    text: '我想',
    time: 1,
    endTime: 1.8,
    words: [
      { word: '我', start: 1, end: 1.4 },
      { word: '想', start: 1.4, end: 1.8 },
    ],
    pronSegsByScript: {
      romaji: [
        { text: 'waga', start: 1, end: 1.4 },
        { text: 'soo', start: 1.4, end: 1.8 },
      ],
    },
  };

  attachPitchNoteLabels(line, notes, 'romaji', 'zh');
  assert.deepEqual(notes.map(note => note.lyric), ['我', '想']);
  assert.deepEqual(notes.map(note => note.pron), [undefined, undefined]);
});

test('Chinese pitch labels keep zero-duration words at note boundaries', () => {
  const notes = [
    { start: 1, end: 1.4 },
    { start: 1.4, end: 1.8 },
  ];
  const line = {
    text: '歌詞',
    time: 0.9,
    endTime: 1.8,
    words: [
      { word: '歌', start: 0.9, end: 0.9 },
      { word: '詞', start: 1.8, end: 1.8 },
    ],
  };

  attachPitchNoteLabels(line, notes, 'romaji', 'zh');
  assert.deepEqual(notes.map(note => note.lyric), ['歌', '詞']);
});

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

test('Japanese score notes keep kanji and attach normalized Hiragana readings', () => {
  const notes = [
    { start: 1, end: 1.5 },
    { start: 1.5, end: 2 },
  ];
  const line = {
    text: '未来',
    words: [
      { word: '未', start: 1, end: 1.5 },
      { word: '来', start: 1.5, end: 2 },
    ],
    pronSegsByScript: {
      kana: [
        { text: 'ミ', start: 1, end: 1.5 },
        { text: 'ライ', start: 1.5, end: 2 },
      ],
    },
  };

  attachPitchNoteLabels(line, notes, 'kana', 'ja');

  assert.deepEqual(notes.map(note => note.lyric), ['未', '来']);
  assert.deepEqual(notes.map(note => note.pron), ['み', 'らい']);
});

test('Japanese score notes show furigana only above kanji and omit duplicate kana', () => {
  const notes = [
    { start: 0, end: 0.5 },
    { start: 0.5, end: 1 },
    { start: 1, end: 1.5 },
    { start: 1.5, end: 2 },
    { start: 2, end: 2.5 },
  ];
  const line = {
    text: 'ない言葉も',
    words: [
      { word: 'な', start: 0, end: 0.5 },
      { word: 'い', start: 0.5, end: 1 },
      { word: '言', start: 1, end: 1.5 },
      { word: '葉', start: 1.5, end: 2 },
      { word: 'も', start: 2, end: 2.5 },
    ],
    pronSegsByScript: {
      kana: [
        { text: 'な', start: 0, end: 0.5 },
        { text: 'い', start: 0.5, end: 1 },
        { text: 'こ', start: 1, end: 1.25 },
        { text: 'と', start: 1.25, end: 1.5 },
        { text: 'ば', start: 1.5, end: 2 },
        { text: 'も', start: 2, end: 2.5 },
      ],
    },
  };

  attachPitchNoteLabels(line, notes, 'kana', 'ja');

  assert.deepEqual(notes.map(note => note.lyric), ['な', 'い', '言', '葉', 'も']);
  assert.deepEqual(notes.map(note => note.pron), [undefined, undefined, 'こと', 'ば', undefined]);
});

test('partial word timing cannot make kanji disappear from score labels', () => {
  const notes = [
    { start: 0, end: 0.5 },
    { start: 0.5, end: 1 },
  ];
  const line = {
    text: '言葉',
    words: [{ word: '言', start: 0, end: 0.5 }],
  };

  attachPitchNoteLabels(line, notes, 'kana', 'ja');

  assert.equal(notes.map(note => note.lyric ?? '').join(''), '言葉');
});

test('duplicated word timing cannot duplicate score lyric glyphs', () => {
  const notes = [
    { start: 0, end: 0.5 },
    { start: 0.5, end: 1 },
  ];
  const line = {
    text: '言葉',
    words: [
      { word: '言', start: 0, end: 0.3 },
      { word: '言', start: 0.3, end: 0.6 },
      { word: '葉', start: 0.6, end: 1 },
    ],
  };

  attachPitchNoteLabels(line, notes, 'kana', 'ja');

  assert.equal(notes.map(note => note.lyric ?? '').join(''), '言葉');
});

test('line text is distributed across notes when word timing is absent', () => {
  const notes = [
    { start: 1, end: 1.4 },
    { start: 1.4, end: 1.8 },
  ];

  attachPitchNoteLabels({ text: '歌詞' }, notes, 'romaji', 'zh');

  assert.deepEqual(notes.map(note => note.lyric), ['歌', '詞']);
});

test('pitch note lyrics render independently from pronunciation position', () => {
  assert.match(
    pipSource,
    /interface PitchNote[\s\S]*?lyric\?: string;[\s\S]*?pron\?: string;/,
  );
  assert.match(pipSource, /if \(n\.pron && noteAttach\)[\s\S]*?fillText\(n\.pron/);
  assert.match(pipSource, /if \(n\.lyric\)[\s\S]*?fillText\(n\.lyric/);
});

test('score canvas draws small pronunciation above the kanji lyric', () => {
  const pronDraw = pipSource.indexOf('ctx.fillText(n.pron');
  const lyricDraw = pipSource.indexOf('ctx.fillText(n.lyric');
  assert.ok(pronDraw >= 0, 'pronunciation draw call is missing');
  assert.ok(lyricDraw >= 0, 'lyric draw call is missing');
  assert.ok(pronDraw < lyricDraw, 'pronunciation must be painted above/before the kanji lyric');
});

test('score furigana is centered over its kanji instead of spilling from the left edge', () => {
  assert.match(
    pipSource,
    /const rubyX = labelX \+ baseLyricWidth \/ 2;[\s\S]*?ctx\.textAlign = 'center';[\s\S]*?ctx\.fillText\(n\.pron, rubyX, pronY\);/,
  );
});

test('pronunciation mode changes are serialized while settings are saved', () => {
  assert.match(pipSource, /this\.pronScriptBtn\.disabled = true/);
  assert.match(pipSource, /await opts\.onPronScriptChange\(next\)/);
  assert.match(pipSource, /finally[\s\S]*?this\.pronScriptBtn\.disabled = false/);
});

test('past lyrics override sung-word accent and current lyrics have no left bar or block', () => {
  const active = ruleBody('.ey-line.active');
  assert.match(active, /background\s*:\s*transparent/);
  assert.match(active, /border-left\s*:\s*0/);

  const pastWord = ruleBody('.ey-line.past .ey-word.sung');
  assert.match(pastWord, /color\s*:\s*inherit/);
  assert.match(pastWord, /text-shadow\s*:\s*none/);
});
