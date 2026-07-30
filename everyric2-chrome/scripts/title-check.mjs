// 제목 파서 자가 검증 — node scripts/title-check.mjs
import assert from 'node:assert/strict';
import { parseSongTitle } from '../src/lib/song-title.ts';

const cases = [
  // [입력, 기대 title, 기대 artist]
  ['G5SH - 舉刀自盡 (Back to Heaven) ft. 莫宰羊 Official Visualizer', '舉刀自盡', 'G5SH'],
  ['G5SH - 舉刀自盡Back to Heaven ft.莫宰羊', '舉刀自盡', 'G5SH'],
  ['G5SH - 放過自己 (Let Go Of Yourself) ft. Marz23 Official Visualizer', '放過自己', 'G5SH'],
  ['G5SH - 兜兜風 ft. Julia Wu 吳卓源 Official Visualizer', '兜兜風', 'G5SH'],
  ['G5SH - 一個人生活 (All by myself) ft. 王艷薇Evangeline Official Visualizer', '一個人生活', 'G5SH'],
  ['Ninajirachi & Porter Robinson - WannaCry', 'WannaCry', 'Ninajirachi & Porter Robinson'],
  ['suis from ヨルシカ - 猫日', '猫日', 'suis from ヨルシカ'],
];
for (const [raw, title, artist] of cases) {
  const p = parseSongTitle(raw);
  assert.equal(p.title, title, `${raw} → title ${p.title}`);
  assert.equal(p.artist, artist, `${raw} → artist ${p.artist}`);
}
console.log('title-check ok');
// 오탐 방지 — 제목 속 'ft' 낱말 조각·진짜 영문 제목은 건드리지 않는다
assert.equal(parseSongTitle('Ninajirachi - Drift Away').title, 'Drift Away');
assert.equal(parseSongTitle('YOASOBI - 夜に駆ける (Official Music Video)').title, '夜に駆ける');
console.log('title-check extra ok');
