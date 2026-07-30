import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const readme = readFileSync(new URL('../../README.md', import.meta.url), 'utf8');

test('fork README distinguishes newly added scoring options from existing features', () => {
  assert.match(readme, /採點顯示方式新增「目標命中音符」顯示模式/);
  assert.match(readme, /唱名標記只新增「關閉」選項/);
  assert.match(readme, /音名表示では「オフ」だけを新たに追加/);
  assert.match(readme, /For solfège, only the Off option is new/);
});

test('fork README does not claim score persistence or existing pitch modes as new', () => {
  assert.doesNotMatch(readme, /新增麥克風即時音高偵測/);
  assert.doesNotMatch(readme, /分數保存|スコア保存|persistent scores/);
  assert.doesNotMatch(readme, /switchable target-note/);
});
