import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const readme = readFileSync(new URL('../../README.md', import.meta.url), 'utf8');

test('README is a Traditional Chinese guide for Chrome extension users', () => {
  assert.match(readme, /^# Everyric2/m);
  assert.match(readme, /## 主要功能/);
  assert.match(readme, /## 此 fork 新增與調整/);
  assert.match(readme, /## 安裝 Chrome 擴充功能/);
  assert.match(readme, /## 基本使用方法/);
  assert.match(readme, /## 注意事項/);
  assert.match(readme, /## 原作者、資料來源與授權/);
  assert.match(readme, /chrome:\/\/extensions/);
  assert.match(readme, /Everyric-Chrome-<版本>\.zip/);
});

test('README omits duplicated language guides and developer-only sections', () => {
  assert.doesNotMatch(readme, /^## 한국어 사용 안내/m);
  assert.doesNotMatch(readme, /^## English Guide/m);
  assert.doesNotMatch(readme, /^## 日本語ガイド/m);
  assert.doesNotMatch(readme, /^## 自架伺服器/m);
  assert.doesNotMatch(readme, /^## 伺服器 API/m);
  assert.doesNotMatch(readme, /^## 設定（環境變數）/m);
  assert.doesNotMatch(readme, /^## CLI/m);
  assert.doesNotMatch(readme, /^## 開發/m);
});

test('fork README distinguishes new scoring options from existing features', () => {
  assert.match(readme, /採點顯示方式新增「目標命中音符」顯示模式/);
  assert.match(readme, /唱名標記只新增「關閉」選項/);
});

test('fork README does not claim score persistence or existing pitch modes as new', () => {
  assert.doesNotMatch(readme, /新增麥克風即時音高偵測/);
  assert.doesNotMatch(readme, /分數保存|スコア保存|persistent scores/);
  assert.doesNotMatch(readme, /switchable target-note/);
});
