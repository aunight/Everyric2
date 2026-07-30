import assert from 'node:assert/strict';
import test from 'node:test';

import {
  nextPronunciationPatch,
  pronunciationChipKey,
  resolvedSongPronSegments,
  resolvedSongPronunciation,
} from '../src/ui/pronunciation-chip.ts';

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

test('Chinese songs render Romanized Pinyin even when the saved global script is Hangul', () => {
  const romajiSegments = [{ text: 'wǒ', start: 1, end: 1.2 }];
  const pinyinLine = {
    text: '我',
    time: 1,
    pronunciation: '워',
    pron: { romaji: 'wǒ' },
    pronSegsByScript: { romaji: romajiSegments },
  };
  assert.equal(resolvedSongPronunciation(pinyinLine, 'zh', 'hangul'), 'wǒ');
  assert.equal(resolvedSongPronSegments(pinyinLine, 'zh', 'hangul'), romajiSegments);

  const legacyPinyin = {
    text: '我',
    time: 1,
    pronunciation: 'wǒ',
    pron: { romaji: '我' },
  };
  assert.equal(resolvedSongPronunciation(legacyPinyin, 'zh', 'hangul'), 'wǒ');
});
