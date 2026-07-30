import type { Judgement } from '../lib/karaoke-score';
import type { MicTracePoint } from '../lib/mic-trace';

export interface DetectedPitchBar {
  midi: number;
  start: number;
  end: number;
  judgement: Judgement | null;
}

const SAMPLE_TAIL_SEC = 0.08;
const MAX_SAMPLE_GAP_SEC = 0.16;
const MERGE_EPSILON_SEC = 0.02;

function roundedTime(value: number): number {
  return Math.round(value * 1000) / 1000;
}

export function buildDetectedPitchBars(points: MicTracePoint[]): DetectedPitchBar[] {
  const sortedPoints = [...points]
    .filter(point => Number.isFinite(point.t) && Number.isFinite(point.midi))
    .sort((a, b) => a.t - b.t);
  const bars: DetectedPitchBar[] = [];

  for (let index = 0; index < sortedPoints.length; index++) {
    const point = sortedPoints[index];
    const next = sortedPoints[index + 1];
    const nextIsContinuous =
      next !== undefined
      && next.t > point.t
      && next.t - point.t <= MAX_SAMPLE_GAP_SEC;
    const bar: DetectedPitchBar = {
      midi: Math.round(point.midi),
      start: roundedTime(point.t),
      end: roundedTime(nextIsContinuous ? next.t : point.t + SAMPLE_TAIL_SEC),
      judgement: point.judgement,
    };
    const previous = bars[bars.length - 1];
    if (
      previous
      && previous.midi === bar.midi
      && previous.judgement === bar.judgement
      && bar.start <= previous.end + MERGE_EPSILON_SEC
    ) {
      previous.end = Math.max(previous.end, bar.end);
    } else {
      bars.push(bar);
    }
  }

  return bars;
}
