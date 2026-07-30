# Artist Display and Search Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve collaborator names in the Chrome extension UI, hide only YouTube’s `- Topic` suffix, and send only the primary artist to lyrics and candidate searches.

**Architecture:** Add one pure artist-name module with separate display and search transformations. Apply display normalization when `SongInfo` is detected, and apply primary-artist normalization inside each external search boundary so automatic searches, manual searches, and old cached values behave consistently without changing returned candidate labels.

**Tech Stack:** TypeScript, Node test runner, Chrome extension service worker, LRCLIB and NetEase HTTP clients

---

## File Structure

- Create `everyric2-chrome/src/lib/artist-name.ts`: shared pure display/search artist transformations.
- Create `everyric2-chrome/tests/artist-name.test.mjs`: functional transformation tests and source-wiring regression assertions.
- Modify `everyric2-chrome/src/lib/song-detector.ts`: normalize detected display artists while preserving collaborators.
- Modify `everyric2-chrome/src/lib/lrclib.ts`: normalize artists before automatic and manual LRCLIB searches.
- Modify `everyric2-chrome/src/lib/netease.ts`: normalize artists before NetEase search and candidate ranking.
- Modify `everyric2-chrome/src/background.ts`: normalize the server link-candidate artist while preserving candidate result labels.

### Task 1: Define Separate Display and Search Transformations

**Files:**
- Create: `everyric2-chrome/src/lib/artist-name.ts`
- Create: `everyric2-chrome/tests/artist-name.test.mjs`

- [ ] **Step 1: Write failing pure-function tests**

Create `everyric2-chrome/tests/artist-name.test.mjs`:

```js
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  artistForDisplay,
  primaryArtistForSearch,
} from '../src/lib/artist-name.ts';

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
  assert.equal(artistForDisplay('A - Topic'), 'A');
  assert.equal(artistForDisplay('A – topic'), 'A');
  assert.equal(artistForDisplay('A — TOPIC'), 'A');
});

test('display artist preserves collaborators and non-Topic suffixes', () => {
  assert.equal(artistForDisplay('A feat. B'), 'A feat. B');
  assert.equal(artistForDisplay('A & B'), 'A & B');
  assert.equal(artistForDisplay('A, B'), 'A, B');
  assert.equal(artistForDisplay('A — Official'), 'A — Official');
});

test('search artist keeps only the first listed artist', () => {
  for (const value of [
    'A feat. B',
    'A Feat B',
    'A featuring B',
    'A ft. B',
    'A FT B',
    'A & B',
    'A, B',
    'A，B',
    'A - Topic',
    'A — Official',
  ]) {
    assert.equal(primaryArtistForSearch(value), 'A', value);
  }
});

test('search artist preserves internal hyphens and slashes', () => {
  assert.equal(primaryArtistForSearch('A-Teens'), 'A-Teens');
  assert.equal(primaryArtistForSearch('AC/DC'), 'AC/DC');
});

test('artist normalization handles blank and leading separators safely', () => {
  assert.equal(artistForDisplay('   '), '');
  assert.equal(primaryArtistForSearch(null), '');
  assert.equal(primaryArtistForSearch('& B'), '');
});
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
cd everyric2-chrome
node --test tests/artist-name.test.mjs
```

Expected: FAIL with `ERR_MODULE_NOT_FOUND` for `src/lib/artist-name.ts`.

- [ ] **Step 3: Implement the pure transformations**

Create `everyric2-chrome/src/lib/artist-name.ts`:

```ts
const TRAILING_TOPIC = /\s+[-–—]\s+topic\s*$/i;
const SEARCH_SEPARATOR =
  /(?:^|\s+)(?:feat(?:uring)?|ft)\.?(?=\s|$)|[&,，]|\s+[-–—]\s+/i;

function normalizeWhitespace(value: string): string {
  return value.replace(/\s{2,}/g, ' ').trim();
}

export function artistForDisplay(raw: string | null | undefined): string {
  return normalizeWhitespace(raw ?? '').replace(TRAILING_TOPIC, '').trim();
}

export function primaryArtistForSearch(raw: string | null | undefined): string {
  const displayArtist = artistForDisplay(raw);
  const separatorIndex = displayArtist.search(SEARCH_SEPARATOR);
  return normalizeWhitespace(
    separatorIndex < 0 ? displayArtist : displayArtist.slice(0, separatorIndex),
  );
}
```

- [ ] **Step 4: Run the pure tests and verify GREEN**

Run:

```bash
cd everyric2-chrome
node --test tests/artist-name.test.mjs
```

Expected: 5 tests PASS with zero failures.

- [ ] **Step 5: Commit the pure module**

```bash
git add everyric2-chrome/src/lib/artist-name.ts \
  everyric2-chrome/tests/artist-name.test.mjs
git commit -m "feat(chrome): separate display and search artist names"
```

### Task 2: Apply Display Normalization to Every Song Detection Path

**Files:**
- Modify: `everyric2-chrome/src/lib/song-detector.ts`
- Modify: `everyric2-chrome/tests/artist-name.test.mjs`

- [ ] **Step 1: Add a failing song-detector wiring test**

Append:

```js
test('every detected SongInfo artist uses display normalization', () => {
  assert.match(songDetectorSource, /import \{ artistForDisplay \} from '\.\/artist-name'/);
  assert.match(
    songDetectorSource,
    /function displayArtist\(value: string \| null \| undefined\): string \| null/,
  );
  assert.match(songDetectorSource, /artistForDisplay\(value\)/);
  assert.equal(
    (songDetectorSource.match(/artist:\s*displayArtist\(/g) ?? []).length,
    3,
  );
  assert.doesNotMatch(songDetectorSource, /\.replace\(\/ - Topic\$\/i/);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd everyric2-chrome
node --test tests/artist-name.test.mjs
```

Expected: FAIL because `song-detector.ts` does not import or call `artistForDisplay`.

- [ ] **Step 3: Normalize artists without removing collaborators**

In `everyric2-chrome/src/lib/song-detector.ts`, add:

```ts
import { artistForDisplay } from './artist-name';
```

Add beside `textOf()`:

```ts
function displayArtist(value: string | null | undefined): string | null {
  return artistForDisplay(value) || null;
}
```

Change the media-session result to:

```ts
artist: displayArtist(split.artist ?? meta.artist),
```

Change the YouTube Music result to:

```ts
artist: displayArtist(parsed.artist ?? artist),
```

Replace the general YouTube channel cleanup with:

```ts
const channel = textOf('#owner #channel-name a').trim() || null;
```

and change the returned artist to:

```ts
artist: displayArtist(split.artist ?? channel),
```

- [ ] **Step 4: Run focused tests and type checking**

Run:

```bash
cd everyric2-chrome
node --test tests/artist-name.test.mjs tests/song-title.test.mjs
npm run typecheck
```

Expected: artist-name and song-title tests PASS; TypeScript exits 0.

- [ ] **Step 5: Commit display normalization**

```bash
git add everyric2-chrome/src/lib/song-detector.ts \
  everyric2-chrome/tests/artist-name.test.mjs
git commit -m "fix(chrome): hide YouTube Topic artist suffix"
```

### Task 3: Normalize Artists at Every External Search Boundary

**Files:**
- Modify: `everyric2-chrome/src/lib/lrclib.ts`
- Modify: `everyric2-chrome/src/lib/netease.ts`
- Modify: `everyric2-chrome/src/background.ts`
- Modify: `everyric2-chrome/tests/artist-name.test.mjs`
- Modify: `everyric2-chrome/tests/netease-priority.test.mjs`

- [ ] **Step 1: Add failing search-boundary tests**

Append to `artist-name.test.mjs`:

```js
test('lyrics and candidate searches derive a primary artist at request time', () => {
  assert.match(lrclibSource, /primaryArtistForSearch/);
  assert.match(neteaseSource, /primaryArtistForSearch/);
  assert.match(
    backgroundSource,
    /artist:\s*primaryArtistForSearch\(message\.payload\.artist\)/,
  );
});

test('candidate result labels keep the full source artist', () => {
  assert.match(backgroundSource, /artist:\s*t\.artistName\s*\?\?\s*''/);
  assert.match(backgroundSource, /artist:\s*nt\.artist/);
});
```

Extend the existing NetEase import in `netease-priority.test.mjs`:

```js
import {
  pickBestNeteaseTrack,
  searchNetease,
} from '../src/lib/netease.ts';
```

Then append:

```js
test('NetEase search sends only the primary artist', async () => {
  const originalFetch = globalThis.fetch;
  let requestedUrl = '';
  globalThis.fetch = async url => {
    requestedUrl = String(url);
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

  assert.equal(
    new URL(requestedUrl).searchParams.get('s'),
    'Main Artist 合作曲',
  );
});
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd everyric2-chrome
node --test tests/artist-name.test.mjs tests/netease-priority.test.mjs
```

Expected: FAIL because LRCLIB, NetEase, and server link-candidate requests do not use `primaryArtistForSearch`.

- [ ] **Step 3: Normalize LRCLIB automatic and manual searches**

In `everyric2-chrome/src/lib/lrclib.ts`, import:

```ts
import { primaryArtistForSearch } from './artist-name';
```

In `fetchFromLrclib()`, derive:

```ts
const artist = primaryArtistForSearch(song.artist);
```

Use the derived value in the exact request:

```ts
if (artist) {
  const params = new URLSearchParams({
    track_name: song.title,
    artist_name: artist,
  });
  if (song.duration > 0) params.set('duration', String(song.duration));
  const exact = await getJSON<LRCLibTrack>(`${BASE}/get?${params}`);
  if (exact && !exact.instrumental) return exact;
}
```

At the start of `searchLrclib()` add:

```ts
const artist = primaryArtistForSearch(song.artist);
```

and construct its attempts with:

```ts
if (artist) {
  attempts.push([new URLSearchParams({
    track_name: song.title,
    artist_name: artist,
  }), false]);
}
if (artist || song.duration > 0) {
  attempts.push([
    new URLSearchParams({ q: artist ? `${artist} ${song.title}` : song.title }),
    true,
  ]);
}
```

At the start of `searchTracksLrclib()` add:

```ts
const artist = primaryArtistForSearch(query.artist);
```

and construct its attempts with:

```ts
if (artist) {
  attempts.push(new URLSearchParams({
    track_name: query.title,
    artist_name: artist,
  }));
}
attempts.push(new URLSearchParams({
  q: artist ? `${artist} ${query.title}` : query.title,
}));
```

Keep the original `SongInfo.artist` and manual input object unchanged.

- [ ] **Step 4: Normalize NetEase search and artist matching**

In `everyric2-chrome/src/lib/netease.ts`, import:

```ts
import { primaryArtistForSearch } from './artist-name';
```

In `searchNetease()`, use:

```ts
const artist = primaryArtistForSearch(query.artist);
const s = artist ? `${artist} ${query.title}` : query.title;
```

In `pickBestNeteaseTrack()`, derive:

```ts
const expectedArtist = primaryArtistForSearch(song.artist);
```

and replace the candidate comparison with:

```ts
const artistMatches = !expectedArtist || textMatches(track.artist, expectedArtist);
```

- [ ] **Step 5: Normalize server link-candidate lookup only**

In `everyric2-chrome/src/background.ts`, import:

```ts
import { primaryArtistForSearch } from './lib/artist-name';
```

Change only the `LINK_CANDIDATES` request:

```ts
artist: primaryArtistForSearch(message.payload.artist),
```

Do not normalize `lookupSync()` metadata backfill, generation, translation, or candidate result objects. Those paths preserve full collaborator information.

- [ ] **Step 6: Run focused tests and type checking**

Run:

```bash
cd everyric2-chrome
node --test tests/artist-name.test.mjs tests/netease-priority.test.mjs
npm run typecheck
```

Expected: all focused tests PASS and TypeScript exits 0.

- [ ] **Step 7: Commit search normalization**

```bash
git add everyric2-chrome/src/lib/lrclib.ts \
  everyric2-chrome/src/lib/netease.ts \
  everyric2-chrome/src/background.ts \
  everyric2-chrome/tests/artist-name.test.mjs \
  everyric2-chrome/tests/netease-priority.test.mjs
git commit -m "fix(chrome): search lyrics by primary artist"
```

### Task 4: Full Regression and Production Verification

**Files:**
- Modify only if a directly related verification failure requires correction.

- [ ] **Step 1: Run the full Chrome test suite**

Run:

```bash
cd everyric2-chrome
npm test
```

Expected: all tests PASS with zero failures.

- [ ] **Step 2: Run type checking and scoring self-check**

Run:

```bash
cd everyric2-chrome
npm run typecheck
node scripts/score-check.mjs
```

Expected: TypeScript exits 0 and the scoring self-check prints `score-check ok`.

- [ ] **Step 3: Build the production extension**

Run:

```bash
cd everyric2-chrome
npm run build
```

Expected: Vite completes successfully and writes `everyric2-chrome/dist`.

- [ ] **Step 4: Verify scope and repository hygiene**

Run:

```bash
git diff --check
git status --short --branch
git log -7 --oneline
```

Expected: no whitespace errors, no uncommitted files, and no RMVPE, Demucs, Dereverb,
translation, lyrics-rendering, or scoring changes in the artist-name commits.

- [ ] **Step 5: Manually verify the two representations**

Load `everyric2-chrome/dist` as an unpacked extension and test metadata equivalent to:

```text
display input: Main Artist feat. Guest
display output: Main Artist feat. Guest
search artist: Main Artist

display input: Main Artist - Topic
display output: Main Artist
search artist: Main Artist
```

Confirm candidate rows still show the complete source-provided artist list.
