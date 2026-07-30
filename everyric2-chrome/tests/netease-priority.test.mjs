import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { lyricsSourceOrder } from '../src/lib/lyrics-source-priority.ts';
import { pickBestNeteaseTrack } from '../src/lib/netease.ts';

const typesSource = readFileSync(new URL('../src/types.ts', import.meta.url), 'utf8');
const overlaySource = readFileSync(new URL('../src/ui/overlay.ts', import.meta.url), 'utf8');
const contentSource = readFileSync(new URL('../src/content.ts', import.meta.url), 'utf8');
const backgroundSource = readFileSync(new URL('../src/background.ts', import.meta.url), 'utf8');

test('NetEase is a selectable lyrics source priority in every locale', () => {
  assert.match(typesSource, /lyricsSourcePriority:\s*'vocaro'\s*\|\s*'lrclib'\s*\|\s*'netease'/);
  assert.match(
    overlaySource,
    /\['netease',\s*t\('overlay\.settings\.sourcePriority\.netease'\)\]/,
  );

  for (const locale of ['zh_TW', 'en', 'ja', 'ko']) {
    const messages = JSON.parse(readFileSync(
      new URL(`../_locales/${locale}/messages.json`, import.meta.url),
      'utf8',
    ));
    assert.ok(
      messages.overlay_settings_sourcePriority_netease?.message,
      `${locale} is missing the NetEase priority label`,
    );
  }
});

test('automatic lyrics lookup wires a dedicated NetEase fetch path', () => {
  assert.match(typesSource, /\{\s*type:\s*'FETCH_NETEASE';\s*payload:\s*SongInfo\s*\}/);
  assert.match(backgroundSource, /case\s+'FETCH_NETEASE'/);
  assert.match(contentSource, /'FETCH_NETEASE'/);
});

test('selected source is tried first without dropping the other fallbacks', () => {
  assert.deepEqual(lyricsSourceOrder('vocaro'), ['vocaro', 'lrclib', 'netease']);
  assert.deepEqual(lyricsSourceOrder('lrclib'), ['lrclib', 'vocaro', 'netease']);
  assert.deepEqual(lyricsSourceOrder('netease'), ['netease', 'vocaro', 'lrclib']);
});

test('automatic NetEase matching rejects unrelated titles and prefers the right recording', () => {
  const tracks = [
    { id: 1, title: '真心話', artist: '別人', duration: 190 },
    { id: 2, title: '完全不同的歌', artist: '李浩瑋', duration: 267 },
    { id: 3, title: '真心话 (Album Version)', artist: '李浩玮 Howard Lee', duration: 267 },
  ];

  assert.equal(pickBestNeteaseTrack(tracks, {
    title: '真心話',
    artist: '李浩瑋 Howard Lee',
    duration: 267,
  })?.id, 3);
  assert.equal(pickBestNeteaseTrack(tracks, {
    title: '不存在',
    artist: null,
    duration: 0,
  }), null);
});

test('NetEase human translations stay scoped to the Chinese language layer', () => {
  assert.match(
    backgroundSource,
    /translationsByLang:\s*hasTranslation\s*\?\s*\{\s*zh:\s*translations\s*\}/,
  );
  assert.match(backgroundSource, /targetLang\s*===\s*'zh'/);
  assert.match(backgroundSource, /translationLang:\s*hasVisibleTranslation\s*\?\s*'zh'/);
  assert.match(contentSource, /data\.source\s*===\s*'netease'.*translationLanguage\s*===\s*'zh'/s);
});
