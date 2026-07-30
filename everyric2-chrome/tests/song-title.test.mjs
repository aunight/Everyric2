import assert from 'node:assert/strict';
import test from 'node:test';

import { parseSongTitle } from '../src/lib/song-title.ts';

test('extracts a Chinese title and drops its English translation and promo suffix', () => {
  assert.deepEqual(
    parseSongTitle(
      '李浩瑋 Howard Lee【真心話 From The Bottom Of Your Heart】 Official Music Video(4K)',
    ),
    { title: '真心話', artist: '李浩瑋 Howard Lee' },
  );
});

test('preserves established artist separators while removing a promo suffix', () => {
  assert.deepEqual(
    parseSongTitle('宇多田ヒカル - First Love (Official Video)'),
    { title: 'First Love', artist: '宇多田ヒカル' },
  );
});

test('removes the common bracketed Official Lyric Video suffix', () => {
  assert.deepEqual(parseSongTitle('Aimer - 残響散歌 [Official Lyric Video]'), {
    title: '残響散歌',
    artist: 'Aimer',
  });
});

test('does not delete an ordinary English title', () => {
  assert.deepEqual(parseSongTitle('The Beatles - Let It Be'), {
    title: 'Let It Be',
    artist: 'The Beatles',
  });
});

test('keeps an unsplit ordinary title unchanged', () => {
  assert.deepEqual(parseSongTitle('Bohemian Rhapsody'), {
    title: 'Bohemian Rhapsody',
    artist: null,
  });
});

test('keeps the first quoted Japanese song title and drops anime opening promotion text', () => {
  assert.deepEqual(
    parseSongTitle(
      'TRUE「Sincerely」 MV Full Size 『ヴァイオレット・エヴァーガーデン』 OP主題歌/"violet-evergarden" Opning Theme「Sincerely」',
    ),
    { title: 'Sincerely', artist: 'TRUE' },
  );
});

test('drops an anime name and opening-theme suffix from a separated Japanese title', () => {
  assert.deepEqual(
    parseSongTitle(
      'TRUE - Sincerely 『ヴァイオレット・エヴァーガーデン』 OP主題歌 Opening Theme',
    ),
    { title: 'Sincerely', artist: 'TRUE' },
  );
});

test('does not treat a Japanese song title in quotes as removable anime metadata', () => {
  assert.deepEqual(parseSongTitle('YOASOBI「アイドル」 Official Music Video'), {
    title: 'アイドル',
    artist: 'YOASOBI',
  });
});
