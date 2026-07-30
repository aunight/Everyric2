// 채점 엔진 자가 검증 — node scripts/score-check.mjs (node 23+의 타입 스트리핑으로 TS를 직접 임포트)
import assert from 'node:assert/strict';
import { ScoreTracker } from '../src/lib/karaoke-score.ts';

const tracker = new ScoreTracker();
// 노트 2개: 0~1초 C4(60), 2~3초 E4(64)
tracker.setNotes([
  { midi: 64, start: 2, end: 3 },
  { midi: 60, start: 0, end: 1 },
]);

// 아직 아무것도 안 지났다 — 점수 없음
assert.equal(tracker.totalScore(), null);

// 첫 노트를 정확히 부른다 (10 샘플)
for (let i = 0; i < 10; i++) {
  const at = i * 0.05;
  const v = tracker.feed(at, 0.1 + i * 0.08, 60);
  assert.equal(v, 'hit');
}
// 같은 at을 다시 먹여도 중복 집계되지 않는다 (판정만 반환)
tracker.feed(0.0, 0.1, 72);
tracker.advance(1.5); // 첫 노트 종료 지점을 지남
assert.equal(Math.round(tracker.totalScore()), 100);

// 옥타브 위로 불러도 명중 (옥타브 불변)
assert.equal(tracker.judge(0.5, 72), 'hit');
// 반음 셋 어긋나면 미스, 반음 하나는 근접
assert.equal(tracker.judge(0.5, 63), 'miss');
assert.equal(tracker.judge(0.5, 61), 'near');
// 노트 밖(간주)은 채점 없음
assert.equal(tracker.judge(1.6, 60), null);

// 두 번째 노트는 완전히 틀리게 부른다 → 길이 같으니 총점 ~50
for (let i = 0; i < 10; i++) {
  tracker.feed(1 + i * 0.05, 2.1 + i * 0.08, 58); // E4 대비 6반음 어긋남
}
tracker.advance(3.5);
assert.equal(Math.round(tracker.totalScore()), 50);

// 안 부른 노트는 침묵이어도 0점 — advance만으로 편입
const silent = new ScoreTracker();
silent.setNotes([{ midi: 60, start: 0, end: 1 }]);
silent.advance(2);
assert.equal(Math.round(silent.totalScore()), 0);

// reset 후엔 처음부터
tracker.reset();
assert.equal(tracker.totalScore(), null);

console.log('score-check ok');
