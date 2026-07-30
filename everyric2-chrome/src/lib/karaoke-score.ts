/** 카라오케 채점 엔진 — 정답 노트(멜로디 전사) vs 마이크 피치 샘플.
 *
 * 순수 로직만 담는다(오디오·DOM 없음) — pip.ts가 렌더 프레임마다 마이크 샘플을
 * 곡 시간으로 사상해 feed()하고, totalScore()/judge()를 그려 준다.
 *
 * 채점 모델(DAM 정밀채점의 음정 항목만 — 안정성·표현력·비브라토는 다음 단계):
 * - 샘플의 곡 시간 ±GRACE_SEC 안에 걸리는 노트가 후보다. 후보가 없으면 채점하지
 *   않는다(간주·애드리브에서 소리 내도 감점 없음).
 * - 음정 오차는 **옥타브 불변**이다 — 성별·음역이 달라 한두 옥타브 이동해 부르는 건
 *   노래방 채점 관례상 정답으로 친다(마이크 옥타브 보정 설정과 별개로 엔진이 항상
 *   가장 가까운 옥타브로 접는다).
 * - 오차 ≤ HIT_ST 반음이면 명중(1.0), ≤ NEAR_ST면 근접(0.5), 그 외 0.
 * - 노트 점수 = 그 노트에 떨어진 샘플들의 평균. 재생이 노트 끝을 지났는데 샘플이
 *   하나도 없으면 0점(안 부르면 침묵이 만점이 되는 걸 막는다).
 * - 총점 = 끝난 노트들의 길이 가중 평균 × 100.
 *
 * 시크·구간 반복은 그냥 허용한다 — 같은 노트를 다시 부르면 샘플이 누적되어 평균이
 * 갱신된다(연습 모드로서 자연스러운 동작). 곡이 바뀌면 호출부가 reset()한다.
 */

export interface ScoreNote {
  midi: number;
  start: number;
  end: number;
}

export type Judgement = 'hit' | 'near' | 'miss';

/** 마이크 입력~스피커 출력 왕복 지연 + 발성 onset 오차 흡수 창(초).
 * ponytail: 고정 상수 — 기기별 실측 보정이 필요해지면 설정으로 뺀다 */
const GRACE_SEC = 0.15;
/** 명중 반음 오차 상한 (75센트) — DAM 기본 판정 폭과 비슷한 관대함 */
const HIT_ST = 0.75;
const NEAR_ST = 1.5;

interface NoteAcc {
  samples: number;
  weight: number; // hit=1, near=0.5 누적
}

export class ScoreTracker {
  private notes: ScoreNote[] = [];
  private acc: NoteAcc[] = [];
  /** 벽시계 기준 마지막으로 먹은 샘플 — MicPitch.samples()는 최근 4초 창이라
   * 프레임마다 겹쳐 온다. at(벽시계)은 단조 증가하므로 이걸로 중복을 거른다 */
  private lastFedAt = -1;
  /** 재생이 지나간 최대 곡 시간 — "끝난 노트" 판정 기준 */
  private playedUntil = 0;

  /** 곡의 정답 노트를 설정하고 채점을 처음부터 시작한다 (start 오름차순 정렬됨) */
  setNotes(notes: ScoreNote[]): void {
    this.notes = [...notes].sort((a, b) => a.start - b.start);
    this.reset();
  }

  reset(): void {
    this.acc = this.notes.map(() => ({ samples: 0, weight: 0 }));
    this.lastFedAt = -1;
    this.playedUntil = 0;
  }

  /** 샘플 하나를 채점한다. at=벽시계 초(중복 제거용), t=곡 시간, midi=마이크 피치.
   * 반환: 이 샘플의 판정 (노트 구간 밖이면 null — 궤적 색칠용) */
  feed(at: number, t: number, midi: number): Judgement | null {
    if (at <= this.lastFedAt) return this.judge(t, midi);
    this.lastFedAt = at;
    if (t > this.playedUntil) this.playedUntil = t;

    const idx = this.bestNoteIndex(t, midi);
    if (idx === null) return null;
    const err = octaveFoldedError(midi, this.notes[idx].midi);
    const a = this.acc[idx];
    a.samples++;
    a.weight += err <= HIT_ST ? 1 : err <= NEAR_ST ? 0.5 : 0;
    return verdict(err);
  }

  /** 채점 없이 판정만 (이미 먹은 샘플을 다시 그릴 때) */
  judge(t: number, midi: number): Judgement | null {
    const idx = this.bestNoteIndex(t, midi);
    if (idx === null) return null;
    return verdict(octaveFoldedError(midi, this.notes[idx].midi));
  }

  /** 재생 위치 갱신 — 마이크가 조용해서 feed가 없어도 "안 부른 노트"가 0점으로
   * 편입되도록 렌더 프레임마다 불러 준다 */
  advance(t: number): void {
    if (t > this.playedUntil) this.playedUntil = t;
  }

  /** 총점 0~100. 아직 끝난 노트가 없으면 null(표시 생략) */
  totalScore(): number | null {
    let dur = 0;
    let sum = 0;
    for (let i = 0; i < this.notes.length; i++) {
      const n = this.notes[i];
      if (n.end > this.playedUntil) break; // start 정렬이라 이후는 전부 미래
      const w = Math.max(0.05, n.end - n.start);
      const a = this.acc[i];
      dur += w;
      sum += w * (a.samples > 0 ? a.weight / a.samples : 0);
    }
    if (dur === 0) return null;
    return (sum / dur) * 100;
  }

  /** t±GRACE 안에서 음정이 가장 가까운 노트 인덱스 — 겹치는 구간(전주 직후 등)에서
   * 시간만으로 고르면 반주 음을 부른 걸로 오판하므로 피치까지 본다 */
  private bestNoteIndex(t: number, midi: number): number | null {
    let best: number | null = null;
    let bestErr = Infinity;
    // notes는 start 정렬 — 선형 탐색이지만 후보 창이 좁아 실측 몇 개 수준.
    // ponytail: O(n) 스캔, 노트 수천 개 곡에서 프레임 예산을 먹으면 이진 탐색으로.
    for (const [i, n] of this.notes.entries()) {
      if (n.start - GRACE_SEC > t) break;
      if (n.end + GRACE_SEC < t) continue;
      const err = octaveFoldedError(midi, n.midi);
      if (err < bestErr) {
        bestErr = err;
        best = i;
      }
    }
    return best;
  }
}

/** 옥타브 접기 오차(반음) — mod 12 원환에서의 최단 거리 */
function octaveFoldedError(micMidi: number, noteMidi: number): number {
  const d = Math.abs(micMidi - noteMidi) % 12;
  return Math.min(d, 12 - d);
}

function verdict(err: number): Judgement {
  return err <= HIT_ST ? 'hit' : err <= NEAR_ST ? 'near' : 'miss';
}
