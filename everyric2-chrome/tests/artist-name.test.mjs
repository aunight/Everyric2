import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  artistForDisplay,
  primaryArtistForSearch,
} from '../src/lib/artist-name.ts';
import { searchNetease } from '../src/lib/netease.ts';

const songDetectorSource = readFileSync(
  new URL('../src/lib/song-detector.ts', import.meta.url),
  'utf8',
);
const lrclibSource = readFileSync(
  new URL('../src/lib/lrclib.ts', import.meta.url),
  'utf8',
);
const neteaseSource = readFileSync(
  new URL('../src/lib/netease.ts', import.meta.url),
  'utf8',
);
const backgroundSource = readFileSync(
  new URL('../src/background.ts', import.meta.url),
  'utf8',
);

test('display artist removes only a trailing YouTube Topic suffix', () => {
  assert.equal(artistForDisplay('Artist - Topic'), 'Artist');
  assert.equal(artistForDisplay('Artist – Topic'), 'Artist');
  assert.equal(artistForDisplay('Artist — topic'), 'Artist');
});

test('display artist keeps collaborators and other channel suffixes', () => {
  assert.equal(artistForDisplay('Artist feat. Guest'), 'Artist feat. Guest');
  assert.equal(artistForDisplay('Artist & Guest'), 'Artist & Guest');
  assert.equal(artistForDisplay('Artist, Guest'), 'Artist, Guest');
  assert.equal(artistForDisplay('Artist - Official'), 'Artist - Official');
});

test('search artist keeps only the first artist across collaborator separators', () => {
  for (const value of [
    'Main Artist feat Guest',
    'Main Artist feat. Guest',
    'Main Artist featuring Guest',
    'Main Artist ft Guest',
    'Main Artist ft. Guest',
    'Main Artist & Guest',
    'Main Artist, Guest',
    'Main Artist，Guest',
    'Main Artist - Guest',
    'Main Artist – Guest',
    'Main Artist — Guest',
  ]) {
    assert.equal(primaryArtistForSearch(value), 'Main Artist', value);
  }
});

test('search artist strips Topic but keeps names with internal punctuation', () => {
  assert.equal(primaryArtistForSearch('Main Artist - Topic'), 'Main Artist');
  assert.equal(primaryArtistForSearch('Main Artist - Official'), 'Main Artist');
  assert.equal(primaryArtistForSearch('A-Teens'), 'A-Teens');
  assert.equal(primaryArtistForSearch('AC/DC'), 'AC/DC');
});

test('artist normalization safely handles blank and malformed input', () => {
  assert.equal(artistForDisplay(null), '');
  assert.equal(artistForDisplay('  '), '');
  assert.equal(primaryArtistForSearch(undefined), '');
  assert.equal(primaryArtistForSearch(' feat. Guest'), '');
  assert.equal(primaryArtistForSearch('  Main   Artist   feat.   Guest  '), 'Main Artist');
});

test('all detected artist display paths use the shared display normalizer', () => {
  assert.match(
    songDetectorSource,
    /import \{ artistForDisplay \} from '\.\/artist-name\.ts'/,
  );
  assert.match(
    songDetectorSource,
    /function displayArtist\(value: string \| null \| undefined\): string \| null/,
  );
  assert.equal(
    songDetectorSource.match(/artist:\s*displayArtist\(/g)?.length,
    3,
  );
  assert.doesNotMatch(songDetectorSource, /\.replace\(\/ - Topic\$\/i/);
});

test('all lyrics search boundaries normalize to the primary artist', () => {
  assert.match(
    lrclibSource,
    /import \{ primaryArtistForSearch \} from '\.\/artist-name\.ts'/,
  );
  assert.match(
    neteaseSource,
    /import \{ primaryArtistForSearch \} from '\.\/artist-name\.ts'/,
  );
  assert.match(
    backgroundSource,
    /import \{ primaryArtistForSearch \} from '\.\/lib\/artist-name\.ts'/,
  );
  assert.match(
    backgroundSource,
    /case 'LINK_CANDIDATES':[\s\S]*?artist:\s*primaryArtistForSearch\(message\.payload\.artist\)/,
  );
});

test('manual candidate labels retain the full artist names returned by each source', () => {
  assert.match(backgroundSource, /artist:\s*t\.artistName\s*\?\?\s*''/);
  assert.match(backgroundSource, /artist:\s*nt\.artist/);
});

test('NetEase requests use only the primary artist', async () => {
  const originalFetch = globalThis.fetch;
  let requestedUrl = '';
  globalThis.fetch = async input => {
    requestedUrl = String(input);
    return {
      ok: true,
      json: async () => ({ result: { songs: [] } }),
    };
  };

  try {
    await searchNetease({
      title: '合作曲',
      artist: 'Main Artist feat. Guest',
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(new URL(requestedUrl).searchParams.get('s'), 'Main Artist 合作曲');
});
