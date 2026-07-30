// MPM 피치 검출 자가 검증 — node scripts/mpm-check.mjs (node 23+ 타입 스트리핑)
import assert from 'node:assert/strict';
import { autoCorrelate } from '../src/lib/mic-pitch.ts';

const SR = 48000;
const N = 2048;
function synth(freqs, amps) {
  const buf = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    for (let k = 0; k < freqs.length; k++) buf[i] += amps[k] * Math.sin(2 * Math.PI * freqs[k] * i / SR);
  }
  return buf;
}
const cents = (a, b) => Math.abs(1200 * Math.log2(a / b));

// 순음 220Hz(A3) — 5센트 이내
assert.ok(cents(autoCorrelate(synth([220], [0.5]), SR), 220) < 5, 'pure A3');
// 저음 90Hz — ACF가 특히 약하던 대역
assert.ok(cents(autoCorrelate(synth([90], [0.5]), SR), 90) < 10, 'low 90Hz');
// 옥타브 함정: 기본파보다 2배음이 훨씬 센 신호 — 맨 ACF가 440으로 오판하던 케이스
const f = autoCorrelate(synth([220, 440, 660], [0.2, 0.6, 0.3]), SR);
assert.ok(cents(f, 220) < 15, `octave trap: got ${f}`);
// 비브라토 흉내(±30센트 변조) — 창 안 평균 근처로 잡히면 된다
const vib = new Float32Array(N);
for (let i = 0; i < N; i++) {
  const inst = 300 * Math.pow(2, (30 / 1200) * Math.sin(2 * Math.PI * 6 * i / SR));
  vib[i] = 0.5 * Math.sin(2 * Math.PI * inst * i / SR);
}
assert.ok(cents(autoCorrelate(vib, SR), 300) < 60, 'vibrato');
// 무음 → -1
assert.equal(autoCorrelate(new Float32Array(N), SR), -1, 'silence');
// 백색잡음 → -1 (불명료 게이트)
const noise = new Float32Array(N).map(() => (Math.random() - 0.5) * 0.4);
assert.equal(autoCorrelate(noise, SR), -1, 'noise rejected');

console.log('mpm-check ok');
