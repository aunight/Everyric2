import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { normalizeTraditionalLyricsData } from '../src/lib/traditional-lyrics.ts';

const contentSource = readFileSync(
  new URL('../src/content.ts', import.meta.url),
  'utf8',
);
const neteaseSource = readFileSync(
  new URL('../src/lib/netease-lyrics.ts', import.meta.url),
  'utf8',
);

test('Everyric Chinese cache becomes Traditional without mutating the input', () => {
  const input = {
    source: 'everyric',
    synced: true,
    lines: [
      {
        time: 1,
        endTime: 2,
        text: '静止了这世界像张照片',
        words: [
          {
            word: '静止',
            start: 1,
            end: 1.4,
            notes: [{ midi: 60, start: 1, end: 1.4 }],
          },
        ],
        notes: [{ midi: 60, start: 1, end: 2 }],
      },
      {
        time: 2,
        endTime: 3,
        text: '点不着的香烟',
      },
    ],
    plainText: '静止了这世界像张照片\n点不着的香烟',
  };
  const original = structuredClone(input);

  const result = normalizeTraditionalLyricsData(input);

  assert.deepEqual(result.lines.map(line => line.text), [
    '靜止了這世界像張照片',
    '點不著的香菸',
  ]);
  assert.equal(result.lines[0]?.words?.[0]?.word, '靜止');
  assert.equal(
    result.plainText,
    '靜止了這世界像張照片\n點不著的香菸',
  );
  assert.deepEqual(result.lines[0]?.notes, input.lines[0]?.notes);
  assert.deepEqual(input, original);
});

test('Japanese originals stay Japanese while Chinese translations become Traditional', () => {
  const input = {
    source: 'everyric',
    synced: true,
    lines: [
      {
        time: 1,
        endTime: 2,
        text: '叶えたい未来がある',
        words: [{ word: '未来', start: 1.2, end: 1.6 }],
        translation: '想要实现的未来',
      },
    ],
    plainText: '叶えたい未来がある',
    translationLang: 'zh',
    translationsByLang: {
      zh: ['想要实现的未来'],
    },
  };

  const result = normalizeTraditionalLyricsData(input);

  assert.equal(result.lines[0]?.text, '叶えたい未来がある');
  assert.equal(result.lines[0]?.words?.[0]?.word, '未来');
  assert.equal(result.plainText, '叶えたい未来がある');
  assert.equal(result.lines[0]?.translation, '想要實現的未來');
  assert.deepEqual(result.translationsByLang?.zh, ['想要實現的未來']);
});

test('non-Chinese captions remain unchanged', () => {
  const input = {
    source: 'caption',
    synced: true,
    lines: [
      {
        time: 1,
        endTime: 2,
        text: 'We do not stop',
        words: [{ word: 'stop', start: 1.5, end: 2 }],
      },
    ],
    plainText: 'We do not stop',
  };

  assert.deepEqual(normalizeTraditionalLyricsData(input), input);
});

test('all loaded lyrics normalize before currentData assignment', () => {
  assert.match(
    contentSource,
    /import \{ normalizeTraditionalLyricsData \} from '\.\/lib\/traditional-lyrics';/,
  );
  assert.match(
    contentSource,
    /function applyLyricsData[\s\S]*stripProductionCredits\(data\)[\s\S]*normalizeTraditionalLyricsData\(data\)[\s\S]*currentData = data/,
  );
});

test('NetEase reuses the common OpenCC converter', () => {
  assert.match(
    neteaseSource,
    /import \{ toTraditionalText \} from '\.\/traditional-lyrics\.ts';/,
  );
  assert.doesNotMatch(neteaseSource, /Converter\(\{ from: 'cn', to: 'tw' \}\)/);
});
