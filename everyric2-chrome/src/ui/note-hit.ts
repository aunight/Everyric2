import type { Judgement, ScoreNote } from '../lib/karaoke-score';
import type { MicTracePoint } from '../lib/mic-trace';

export interface NoteHitSegment {
  noteIndex: number;
  midi: number;
  start: number;
  end: number;
  judgement: Judgement;
}

/** 與 ScoreTracker 相同的輸入延遲／起音寬容窗。 */
const GRACE_SEC = 0.15;
/** 最後一個樣本仍要留下可見短段，不能只成為零寬度點。 */
const SAMPLE_TAIL_SEC = 0.08;
/** 超過這個間隔代表中間沒有可靠音高，不替空白區段上色。 */
const MAX_SAMPLE_GAP_SEC = 0.16;
const MERGE_EPSILON_SEC = 0.02;

function octaveFoldedError(micMidi: number, noteMidi: number): number {
  const distance = Math.abs(micMidi - noteMidi) % 12;
  return Math.min(distance, 12 - distance);
}

function bestNoteIndex(point: MicTracePoint, notes: ScoreNote[]): number | null {
  let best: number | null = null;
  let bestError = Infinity;
  for (const [index, note] of notes.entries()) {
    if (note.start - GRACE_SEC > point.t) break;
    if (note.end + GRACE_SEC < point.t) continue;
    const error = octaveFoldedError(point.midi, note.midi);
    if (error < bestError) {
      best = index;
      bestError = error;
    }
  }
  return best;
}

function roundedTime(value: number): number {
  return Math.round(value * 1000) / 1000;
}

/**
 * 把已判定的麥克風樣本裁成目標音符上的短色帶。
 *
 * 樣本可以在 ScoreTracker 的寬容窗內被判定，但真正繪製時仍會裁在音符本體內；
 * 這樣起音延遲不扣掉判定，畫面也不會把顏色溢出到相鄰音符。
 */
export function buildNoteHitSegments(
  points: MicTracePoint[],
  notes: ScoreNote[],
): NoteHitSegment[] {
  const sortedPoints = [...points].sort((a, b) => a.t - b.t);
  const segments: NoteHitSegment[] = [];

  for (let index = 0; index < sortedPoints.length; index++) {
    const point = sortedPoints[index];
    if (point.judgement === null) continue;
    const noteIndex = bestNoteIndex(point, notes);
    if (noteIndex === null) continue;
    const note = notes[noteIndex];
    const next = sortedPoints[index + 1];
    const nextIsContinuous =
      next !== undefined
      && next.t > point.t
      && next.t - point.t <= MAX_SAMPLE_GAP_SEC;
    const start = Math.max(note.start, point.t);
    const end = Math.min(
      note.end,
      nextIsContinuous ? next.t : point.t + SAMPLE_TAIL_SEC,
    );
    if (end <= start) continue;

    const segment: NoteHitSegment = {
      noteIndex,
      midi: note.midi,
      start: roundedTime(start),
      end: roundedTime(end),
      judgement: point.judgement,
    };
    const previous = segments[segments.length - 1];
    if (
      previous
      && previous.noteIndex === segment.noteIndex
      && previous.judgement === segment.judgement
      && segment.start <= previous.end + MERGE_EPSILON_SEC
    ) {
      previous.end = Math.max(previous.end, segment.end);
    } else {
      segments.push(segment);
    }
  }

  return segments;
}
