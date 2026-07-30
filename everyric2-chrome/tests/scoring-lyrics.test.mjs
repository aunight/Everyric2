import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  lyricsForScoring,
  stripScoringBrackets,
} from '../src/lib/scoring-lyrics.ts';

const pipSource = readFileSync(
  new URL('../src/ui/pip.ts', import.meta.url),
  'utf8',
);

test('all supported balanced bracket fragments are removed', () => {
  assert.equal(
    stripScoringBrackets('主歌 (echo) （和聲） [English] 【口號】 尾聲'),
    '主歌 尾聲',
  );
});

test('unmatched brackets remain visible instead of deleting the rest of a line', () => {
  assert.equal(stripScoringBrackets('歌詞（未完'), '歌詞（未完');
  assert.equal(stripScoringBrackets('歌詞 ending)'), '歌詞 ending)');
});

test('bracket-only lines become same-index invisible placeholders', () => {
  const input = [
    {
      time: 2,
      endTime: 3,
      text: "(We don't, we don't)",
      words: [
        {
          word: "(We don't, we don't)",
          start: 2,
          end: 3,
          notes: [{ midi: 62, start: 2, end: 3 }],
        },
      ],
      notes: [{ midi: 62, start: 2, end: 3 }],
      translation: '（我們不會）',
      pronunciation: '(wi dont)',
      pronSegments: [{ text: 'wi', start: 2, end: 2.4 }],
      pron: { romaji: '(wi dont)' },
      pronSegsByScript: {
        romaji: [{ text: 'wi', start: 2, end: 2.4 }],
      },
    },
  ];
  const original = structuredClone(input);

  const result = lyricsForScoring(input);

  assert.equal(result.length, input.length);
  assert.equal(result[0]?.time, 2);
  assert.equal(result[0]?.endTime, 3);
  assert.equal(result[0]?.text, '');
  assert.deepEqual(result[0]?.words, []);
  assert.deepEqual(result[0]?.notes, []);
  assert.equal(result[0]?.translation, undefined);
  assert.equal(result[0]?.pronunciation, undefined);
  assert.deepEqual(result[0]?.pronSegments, []);
  assert.equal(result[0]?.pron, undefined);
  assert.equal(result[0]?.pronSegsByScript, undefined);
  assert.deepEqual(input, original);
});

test('mixed lines keep visible text, matching words, translation, and pitch notes', () => {
  const input = [
    {
      time: 4,
      endTime: 6,
      text: 'Forever (yeah) tonight 【echo】',
      words: [
        { word: 'Forever', start: 4, end: 4.6 },
        { word: '(yeah)', start: 4.6, end: 5 },
        { word: 'tonight', start: 5, end: 5.7 },
        { word: '【echo】', start: 5.7, end: 6 },
      ],
      notes: [
        { midi: 60, start: 4, end: 4.6 },
        { midi: 62, start: 4.6, end: 5.2 },
        { midi: 64, start: 5.2, end: 6 },
      ],
      translation: '永遠（耶）直到今晚【回音】',
      pronunciation: 'forever (yeah) tonight',
    },
  ];

  const result = lyricsForScoring(input);

  assert.equal(result[0]?.text, 'Forever tonight');
  assert.deepEqual(
    result[0]?.words?.map(word => word.word),
    ['Forever', 'tonight'],
  );
  assert.equal(result[0]?.translation, '永遠直到今晚');
  assert.equal(result[0]?.pronunciation, 'forever tonight');
  assert.equal(result[0]?.notes?.length, 3);
});

export { pipSource };
