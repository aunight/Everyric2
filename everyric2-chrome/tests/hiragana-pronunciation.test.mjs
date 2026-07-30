import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  resolvedPronSegments,
  resolvedPronunciation,
} from '../src/lib/lang.ts';

const readLocale = (locale) => JSON.parse(
  readFileSync(new URL(`../_locales/${locale}/messages.json`, import.meta.url), 'utf8'),
);

test('old cached Katakana pronunciation is displayed as Hiragana without replacing kanji lyrics', () => {
  const line = {
    text: '僕は歌う',
    pron: { kana: 'ボク ワ ウタウ' },
  };

  assert.equal(resolvedPronunciation(line, 'kana'), 'ぼく わ うたう');
  assert.equal(line.text, '僕は歌う');
  assert.equal(line.pron.kana, 'ボク ワ ウタウ');
});

test('old cached Katakana timing segments are copied and displayed as Hiragana', () => {
  const source = [
    { text: 'ボ', start: 1, end: 1.2, confidence: 0.9 },
    { text: 'ク', start: 1.2, end: 1.4, resolved: true },
  ];
  const line = {
    text: '僕',
    pronSegsByScript: { kana: source },
  };

  assert.deepEqual(resolvedPronSegments(line, 'kana'), [
    { text: 'ぼ', start: 1, end: 1.2, confidence: 0.9 },
    { text: 'く', start: 1.2, end: 1.4, resolved: true },
  ]);
  assert.deepEqual(source.map(segment => segment.text), ['ボ', 'ク']);
});

test('non-Hiragana pronunciation scripts remain unchanged', () => {
  const hangul = '보쿠와 우타우';
  const romajiSegments = [{ text: 'boku', start: 1, end: 1.4 }];
  const line = {
    text: '僕は歌う',
    pron: { hangul, romaji: 'boku wa utau' },
    pronSegsByScript: { romaji: romajiSegments },
  };

  assert.equal(resolvedPronunciation(line, 'hangul'), hangul);
  assert.equal(resolvedPronunciation(line, 'romaji'), 'boku wa utau');
  assert.equal(resolvedPronSegments(line, 'romaji'), romajiSegments);
});

test('settings and scoring controls explicitly name Hiragana', () => {
  const expected = {
    zh_TW: ['平假名', '切換發音標記（韓文／羅馬字／平假名）'],
    en: ['Hiragana', 'Switch pronunciation script (Hangul/Romaji/Hiragana)'],
    ja: ['ひらがな', '発音表記の切替（ハングル／ローマ字／ひらがな）'],
    ko: ['히라가나', '발음 표기 전환 (한글/로마자/히라가나)'],
  };

  for (const [locale, [option, tooltip]] of Object.entries(expected)) {
    const messages = readLocale(locale);
    assert.equal(messages.overlay_settings_pronScript_kana.message, option);
    assert.equal(messages.pip_controls_pronScriptToggle.message, tooltip);
  }

  const pip = readFileSync(new URL('../src/ui/pip.ts', import.meta.url), 'utf8');
  assert.match(pip, /this\.pronScript === 'romaji' \? 'Ro' : 'ひら'/);
  assert.doesNotMatch(pip, /'カナ'/);
});
