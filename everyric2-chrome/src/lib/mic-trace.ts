import type { Judgement } from './karaoke-score';

export interface MicTracePoint {
  t: number;
  midi: number;
  judgement: Judgement | null;
}

export interface MicTraceSegment {
  from: MicTracePoint;
  to: MicTracePoint;
  judgement: Judgement | null;
}

/** 45ms 샘플 3개 이상을 여유 있게 잇되, 실제 무음/검출 공백은 선으로 메우지 않는다. */
const MAX_GAP_SEC = 0.16;
/** 순간 옥타브 오검출이나 다른 배음으로 튄 점을 긴 대각선으로 연결하지 않는다. */
const MAX_JUMP_ST = 4;

/** 시간·음정이 실제로 이어지는 마이크 샘플 쌍만 일 K식 선분으로 만든다. */
export function buildMicTraceSegments(points: MicTracePoint[]): MicTraceSegment[] {
  const segments: MicTraceSegment[] = [];
  for (let i = 1; i < points.length; i++) {
    const from = points[i - 1];
    const to = points[i];
    if (
      to.t - from.t <= MAX_GAP_SEC
      && Math.abs(to.midi - from.midi) <= MAX_JUMP_ST
    ) {
      segments.push({ from, to, judgement: to.judgement });
    }
  }
  return segments;
}
