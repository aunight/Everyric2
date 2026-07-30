import { resolvedPronSegments, type PronScript } from '../lib/lang.ts';
import type { LyricLanguage } from '../lib/translation-visibility';
import type { LyricLine } from '../types';
import { buildKanjiRubyReadings } from './karaoke.ts';

export interface PitchLabelNote {
  start: number;
  end: number;
  lyric?: string;
  pron?: string;
}

type PitchLabelField = 'lyric' | 'pron';

function appendLabel(note: PitchLabelNote, field: PitchLabelField, text: string): void {
  note[field] = note[field] ? note[field] + text : text;
}

function appendToBestOverlappingNote(
  notes: PitchLabelNote[],
  field: PitchLabelField,
  text: string,
  start: number,
  end: number,
): boolean {
  let best: PitchLabelNote | null = null;
  let bestOverlap = 0;
  for (const note of notes) {
    const overlap = Math.min(note.end, end) - Math.max(note.start, start);
    if (overlap > bestOverlap) {
      bestOverlap = overlap;
      best = note;
    }
  }
  if (best) appendLabel(best, field, text);
  return best !== null;
}

function appendToNearestNote(
  notes: PitchLabelNote[],
  field: PitchLabelField,
  text: string,
  start: number,
  end: number,
): void {
  let best: PitchLabelNote | null = null;
  let bestDistance = Infinity;
  let bestForwardPenalty = Infinity;
  const midpoint = (start + end) / 2;
  let bestCenterDistance = Infinity;

  for (const note of notes) {
    const distance =
      end < note.start ? note.start - end
      : start > note.end ? start - note.end
      : 0;
    // 零長度字剛好落在兩個音符交界時，優先貼到從該時刻開始的下一個音符。
    const forwardPenalty = note.start >= end ? 0 : 1;
    const centerDistance = Math.abs((note.start + note.end) / 2 - midpoint);
    if (
      distance < bestDistance
      || (
        distance === bestDistance
        && (
          forwardPenalty < bestForwardPenalty
          || (
            forwardPenalty === bestForwardPenalty
            && centerDistance < bestCenterDistance
          )
        )
      )
    ) {
      best = note;
      bestDistance = distance;
      bestForwardPenalty = forwardPenalty;
      bestCenterDistance = centerDistance;
    }
  }

  if (best) appendLabel(best, field, text);
}

/**
 * 採點音符上的文字標籤。
 *
 * 中文歌固定使用原文，不能讀取全域 hangul/romaji/kana 設定；舊資料可能已把中文
 * 漢字誤判為日文並存入 waga、soo 等日文讀音，沿用該欄位會把錯字直接畫到音符上。
 * 其他語言維持原本的發音音節標籤。
 */
export function attachPitchNoteLabels(
  line: LyricLine,
  notes: PitchLabelNote[],
  script: PronScript,
  songLanguage: LyricLanguage,
): void {
  // 原文與發音是兩個獨立圖層。原文永遠先配到音符，不能再受發音位置或資料有無影響。
  for (const word of line.words ?? []) {
    const label = word.word.replace(/\s+/g, '');
    if (!label) continue;
    if (!appendToBestOverlappingNote(notes, 'lyric', label, word.start, word.end)) {
      appendToNearestNote(notes, 'lyric', label, word.start, word.end);
    }
  }
  // words가 일부 누락되거나 중복돼도 원문 글자는 정확히 한 번씩 보여야 한다.
  // 매핑 결과가 원문과 완전히 같지 않으면 부분 결과를 버리고 원문 순서로 재분배한다.
  const glyphs = Array.from(line.text.replace(/\s+/g, ''));
  const mappedLyrics = notes.map(note => note.lyric ?? '').join('');
  if (notes.length > 0 && mappedLyrics !== glyphs.join('')) {
    for (const note of notes) note.lyric = undefined;
    for (const [index, glyph] of glyphs.entries()) {
      const noteIndex = Math.min(
        notes.length - 1,
        Math.floor(index * notes.length / Math.max(1, glyphs.length)),
      );
      appendLabel(notes[noteIndex], 'lyric', glyph);
    }
  }

  // 中文固定只用漢字原文，避免舊資料裡誤判成日文的 waga/soo 等讀音再次出現。
  if (songLanguage === 'zh') return;

  const pronSegments = resolvedPronSegments(line, script) ?? [];
  if (songLanguage === 'ja' && script === 'kana') {
    // 日文平假名模式只替漢字建立振假名。原文本來就是假名時不再畫一份相同讀音，
    // 避免畫面出現「な/な」「い/い」這種上下重複。
    const rubyReadings = buildKanjiRubyReadings(line, pronSegments);
    for (const [word, reading] of rubyReadings) {
      const label = reading.replace(/\s+/g, '');
      if (!label) continue;
      if (!appendToBestOverlappingNote(notes, 'pron', label, word.start, word.end)) {
        appendToNearestNote(notes, 'pron', label, word.start, word.end);
      }
    }
    return;
  }

  for (const segment of pronSegments) {
    appendToBestOverlappingNote(notes, 'pron', segment.text, segment.start, segment.end);
  }
}
