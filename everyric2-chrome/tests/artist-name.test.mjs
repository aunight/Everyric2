import assert from 'node:assert/strict';
import test from 'node:test';

import {
  artistForDisplay,
  primaryArtistForSearch,
} from '../src/lib/artist-name.ts';

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
