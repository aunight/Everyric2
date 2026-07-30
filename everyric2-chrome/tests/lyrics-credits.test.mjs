import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import test, { after } from 'node:test';

const tempDir = mkdtempSync(join(tmpdir(), 'everyric2-lyrics-clean-'));
const modulePath = join(tempDir, 'lyrics-clean.ts');
const source = readFileSync(new URL('../src/lib/lyrics-clean.ts', import.meta.url), 'utf8')
  .replace("import { t } from './i18n';", "const t = (key: string) => key;");
writeFileSync(modulePath, source);
const lyricsClean = await import(pathToFileURL(modulePath).href);

after(() => rmSync(tempDir, { recursive: true, force: true }));

test('recognizes production credits that use Japanese or Latin separators', () => {
  for (const line of [
    '編曲：堀江晶太 / Evan Call',
    '編曲・堀江晶太 / Evan Call',
    '作詞・作曲：TRUE',
    '混音：王小明',
    'Lyrics & Music: Evan Call',
    'Mixed by: John Smith',
  ]) {
    assert.equal(lyricsClean.partMarkerKind(line), 'credit', line);
  }
});

test('does not remove lyric sentences that merely contain a production word', () => {
  for (const line of [
    '作曲なんて知らない',
    '君の声：僕の歌',
    '混音',
    'Music is my life',
  ]) {
    assert.equal(lyricsClean.partMarkerKind(line), null, line);
  }
});

test('removes credit lines and their attached metadata from loaded lyrics data', () => {
  assert.equal(typeof lyricsClean.stripProductionCredits, 'function');

  const data = {
    source: 'everyric',
    synced: true,
    plainText: '編曲・堀江晶太 / Evan Call\n毎當學到未知的話語時',
    lines: [
      {
        time: 0,
        endTime: 2,
        text: '編曲・堀江晶太 / Evan Call',
        pronunciation: 'へんきょく ほりえ あきら ふとし',
      },
      {
        time: 2,
        endTime: 5,
        text: '毎當學到未知的話語時',
        translation: '每當學到未知的話語時',
      },
    ],
    translationsByLang: {
      zh: ['編曲：堀江晶太 / Evan Call', '每當學到未知的話語時'],
      en: ['Arrangement: Evan Call', 'Whenever I learn an unknown word'],
    },
  };

  const cleaned = lyricsClean.stripProductionCredits?.(data) ?? data;
  assert.deepEqual(cleaned.lines, [data.lines[1]]);
  assert.equal(cleaned.plainText, '毎當學到未知的話語時');
  assert.deepEqual(cleaned.translationsByLang, {
    zh: ['每當學到未知的話語時'],
    en: ['Whenever I learn an unknown word'],
  });
});

test('filters every loaded lyrics source before it reaches the display and scoring engine', () => {
  const content = readFileSync(new URL('../src/content.ts', import.meta.url), 'utf8');
  assert.match(content, /stripProductionCredits\(data\)/);
  assert.match(
    content,
    /function applyLyricsData[\s\S]*?stripProductionCredits\(data\)[\s\S]*?currentData = data/,
  );
});

test('drops malformed translation layers when credit filtering changes line indices', () => {
  const cleaned = lyricsClean.stripProductionCredits({
    source: 'everyric',
    synced: true,
    plainText: '作曲：Someone\n歌詞',
    lines: [{ text: '作曲：Someone' }, { text: '歌詞' }],
    translationsByLang: { en: ['wrong length'] },
  });

  assert.deepEqual(cleaned.lines, [{ text: '歌詞' }]);
  assert.deepEqual(cleaned.translationsByLang, {});
});
