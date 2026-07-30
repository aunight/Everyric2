import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  detectLyricLanguage,
  isSameLanguageTarget,
  visibleTranslations,
} from '../src/lib/translation-visibility.ts';

test('hides Chinese translations under Chinese originals', () => {
  const lines = [
    { text: '我想我一直都在', translation: '我想我一直都在' },
    { text: '逃避著我的真心話', translation: '逃避著我的真心話' },
  ];
  assert.deepEqual(visibleTranslations(lines), [undefined, undefined]);
});

test('keeps Chinese translations under Japanese originals including kanji-only lines', () => {
  const lines = [
    { text: '君の声が聞こえる', translation: '我聽得見你的聲音' },
    { text: '想法', translation: '想法' },
  ];
  assert.equal(detectLyricLanguage(lines.map(line => line.text)), 'ja');
  assert.deepEqual(visibleTranslations(lines), ['我聽得見你的聲音', undefined]);
});

test('hides same-language Korean translations', () => {
  assert.deepEqual(
    visibleTranslations([{ text: '너를 사랑해', translation: '나는 너를 사랑해' }]),
    [undefined],
  );
});

test('keeps cross-language translations and hides only normalized duplicate lines', () => {
  const lines = [
    { text: 'Hello, world!', translation: '你好，世界！' },
    { text: 'Same line!', translation: ' same line ' },
  ];
  assert.deepEqual(visibleTranslations(lines), ['你好，世界！', undefined]);
});

test('keeps translations when either corpus language is unknown', () => {
  assert.deepEqual(visibleTranslations([{ text: '♪', translation: '器樂' }]), ['器樂']);
});

test('recognizes Chinese as its own translation target', () => {
  assert.equal(isSameLanguageTarget(['我想我一直都在'], 'zh'), true);
  assert.equal(isSameLanguageTarget(['君の声'], 'zh'), false);
});

test('all lyrics surfaces use the shared visibility rule', () => {
  const overlay = readFileSync(new URL('../src/ui/overlay.ts', import.meta.url), 'utf8');
  const panels = readFileSync(new URL('../src/ui/panels.ts', import.meta.url), 'utf8');
  const pip = readFileSync(new URL('../src/ui/pip.ts', import.meta.url), 'utf8');

  assert.ok((overlay.match(/visibleTranslations\(/g) ?? []).length >= 2);
  assert.match(panels, /visibleTranslations\(lines\)/);
  assert.match(pip, /visibleTranslation\?: string/);
  assert.match(pip, /visibleTranslations\(lines\)/);
  assert.doesNotMatch(pip, /const hasTr = pages\.some\(p => p\.line\.translation\)/);
});

test('translation loading uses the display-language guard', () => {
  const content = readFileSync(new URL('../src/content.ts', import.meta.url), 'utf8');
  assert.match(content, /isSameLanguageTarget\(srcLines,\s*lang\)/);
});
