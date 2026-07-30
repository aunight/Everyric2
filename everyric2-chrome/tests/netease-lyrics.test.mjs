import assert from 'node:assert/strict';
import test from 'node:test';

import { neteaseToLyricsData } from '../src/lib/netease-lyrics.ts';

test('Chinese NetEase originals become Traditional Chinese everywhere', () => {
  const data = neteaseToLyricsData({
    lrc: [
      '[00:01.00]静止了这世界像张照片',
      '[00:02.00]点不着的香烟',
    ].join('\n'),
    tlyric: null,
  }, 'zh');

  assert.deepEqual(data?.lines.map(line => line.text), [
    '靜止了這世界像張照片',
    '點不著的香菸',
  ]);
  assert.equal(
    data?.plainText,
    '靜止了這世界像張照片\n點不著的香菸',
  );
});

test('Chinese timed words and their composed line use the same Traditional text', () => {
  const data = neteaseToLyricsData({
    lrc: [
      '[00:01.00]',
      '<00:01.00>点',
      '<00:01.20>不着',
      '<00:01.40>的',
      '<00:01.60>香烟',
    ].join(''),
    tlyric: null,
  }, 'zh');

  assert.equal(data?.lines[0]?.text, '點 不著 的 香菸');
  assert.deepEqual(
    data?.lines[0]?.words?.map(word => word.word),
    ['點', '不著', '的', '香菸'],
  );
  assert.equal(data?.plainText, '點 不著 的 香菸');
});

test('Japanese originals keep Japanese kanji while Chinese translations become Traditional', () => {
  const data = neteaseToLyricsData({
    lrc: [
      '[00:01.00]叶えたい未来がある',
      '[00:02.00]君の声',
    ].join('\n'),
    tlyric: [
      '[00:01.00]想要实现的未来',
      '[00:02.00]你的声音',
    ].join('\n'),
  }, 'zh');

  assert.deepEqual(data?.lines.map(line => line.text), [
    '叶えたい未来がある',
    '君の声',
  ]);
  assert.deepEqual(data?.lines.map(line => line.translation), [
    '想要實現的未來',
    '你的聲音',
  ]);
  assert.deepEqual(data?.translationsByLang?.zh, [
    '想要實現的未來',
    '你的聲音',
  ]);
});

test('Chinese translation data is retained but hidden from non-Chinese target lines', () => {
  const data = neteaseToLyricsData({
    lrc: '[00:01.00]叶えたい未来がある',
    tlyric: '[00:01.00]想要实现的未来',
  }, 'en');

  assert.equal(data?.lines[0]?.translation, undefined);
  assert.equal(data?.translationsByLang?.zh?.[0], '想要實現的未來');
  assert.equal(data?.humanTranslated, false);
  assert.equal(data?.translationLang, undefined);
});
