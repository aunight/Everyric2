import type { EveryricSegment, LyricLine, WordSegment } from '../types';

const LINE_TIME_RE = /\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;
const WORD_TOKEN_RE = /<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>([^<]*)/g;

function toSeconds(min: string, sec: string, frac?: string): number {
  const fracSec = frac ? Number(`0.${frac}`) : 0;
  return Number(min) * 60 + Number(sec) + fracSec;
}

/** LRC 워터마크/광고 줄 — 줄 전체가 장식문자로 감싼 URL·사이트명일 때만 매칭한다
 *  (가사 본문에 URL이 나오는 건 사실상 없고, 과소 제거 원칙은 lyrics-clean과 동일) */
const LRC_WATERMARK_RE =
  /^[\s\-–—=*~·・.]*(?:https?:\/\/)?(?:www\.)?[a-z0-9-]+(?:\.[a-z]{2,})+(?:\/\S*)?[\s\-–—=*~·・.]*$/i;

export function parseLRC(lrc: string): LyricLine[] {
  const lines: LyricLine[] = [];

  for (const raw of lrc.split('\n')) {
    LINE_TIME_RE.lastIndex = 0;
    const times: number[] = [];
    let bodyStart = 0;
    let m: RegExpExecArray | null;
    while ((m = LINE_TIME_RE.exec(raw)) !== null) {
      if (m.index !== bodyStart) break;
      times.push(toSeconds(m[1], m[2], m[3]));
      bodyStart = LINE_TIME_RE.lastIndex;
    }
    if (times.length === 0) continue;

    const { text, words } = parseWordTimings(raw.slice(bodyStart).trim());
    if (!text) continue;
    if (LRC_WATERMARK_RE.test(text)) continue; // "--- www.LRCgenerator.com ---" 류 워터마크
    for (const time of times) {
      // 반복 타임스탬프 라인끼리 words 배열을 공유하지 않도록 복제
      lines.push({ time, endTime: null, text, words: words?.map(w => ({ ...w })) });
    }
  }

  lines.sort((a, b) => (a.time ?? 0) - (b.time ?? 0));
  for (let i = 0; i < lines.length; i++) {
    lines[i].endTime = i + 1 < lines.length ? lines[i + 1].time : null;
  }
  return lines;
}

function parseWordTimings(body: string): { text: string; words?: WordSegment[] } {
  WORD_TOKEN_RE.lastIndex = 0;
  const words: WordSegment[] = [];
  let m: RegExpExecArray | null;
  while ((m = WORD_TOKEN_RE.exec(body)) !== null) {
    const word = m[4].trim();
    if (!word) continue;
    words.push({ word, start: toSeconds(m[1], m[2], m[3]), end: 0 });
  }
  if (words.length === 0) {
    return { text: body.replace(/<[^>]*>/g, ' ').replace(/\s{2,}/g, ' ').trim() };
  }
  for (let i = 0; i < words.length; i++) {
    words[i].end = i + 1 < words.length ? words[i + 1].start : words[i].start + 1;
  }
  return { text: words.map(w => w.word).join(' '), words };
}

export function parsePlainLyrics(text: string): LyricLine[] {
  return text
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)
    .map(line => ({ time: null, endTime: null, text: line }));
}

export interface SegmentsToLinesResult {
  lines: LyricLine[];
  /** translationsByLang을 lines와 같은 순서로 재정렬한 값 — 두 번째 인자를 안 주면 undefined */
  translationsByLang?: Record<string, (string | undefined)[]>;
}

/**
 * translationsByLang(있으면)은 서버가 보낸 **원본 timestamps 순서·인덱스** 기준이다.
 * 이 함수가 유효성 필터(시작 시각·빈 텍스트)와 시간순 정렬을 거치며 순서를 바꾸므로,
 * 그대로 lines[i]에 대응시키면 필터·정렬로 인덱스가 어긋난다 — 원본 인덱스를 함께
 * 들고 있다가 최종 순서로 재배치해야 한다(V2 확장, 감사 지시: "lyrics-parser 매핑").
 */
export function segmentsToLines(
  segments: EveryricSegment[],
  translationsByLang?: Record<string, (string | null)[]> | null,
): SegmentsToLinesResult {
  // 이진 탐색(SyncEngine)이 시간 오름차순을 전제하므로 서버 순서를 믿지 않고 정렬.
  // originalIndex를 같이 들고 다녀야 translationsByLang을 올바른 세그에 대응시킬 수 있다.
  const valid = segments
    .map((s, originalIndex) => ({ s, originalIndex }))
    .filter(({ s }) => typeof s.start === 'number' && (s.text ?? '').trim().length > 0)
    .sort((a, b) => a.s.start - b.s.start);
  const lines = valid.map(({ s }, i) => ({
    time: s.start,
    endTime: s.end ?? valid[i + 1]?.s.start ?? null,
    text: s.text.trim(),
    words: s.words && s.words.length > 0 ? s.words : undefined,
    notes: s.notes && s.notes.length > 0 ? s.notes : undefined,
    pronunciation: s.pronunciation || undefined,
    pronSegments: s.pron_segments && s.pron_segments.length > 0 ? s.pron_segments : undefined,
    // 표기별 발음(hangul/romaji/kana) — 아직 서버가 안 보내는 필드라 대부분 undefined로
    // 남고, 그 경우 소비처(lib/lang.ts 폴백 헬퍼)가 위 레거시 필드로 자동 폴백한다
    pron: s.pron && Object.keys(s.pron).length > 0 ? s.pron : undefined,
    pronSegsByScript: s.pron_segs && Object.keys(s.pron_segs).length > 0 ? s.pron_segs : undefined,
    translation: s.translation || undefined,
    confidence: s.confidence,
    debug: s.debug
      ? {
          activeRatio: s.debug.active_ratio,
          clamped: s.debug.clamped,
          orig: s.debug.orig,
          fixes: s.debug.fixes,
          heard: s.debug.heard,
          heard_spans: s.debug.heard_spans,
          referee: s.debug.referee,
        }
      : undefined,
  }));

  let reindexed: Record<string, (string | undefined)[]> | undefined;
  if (translationsByLang) {
    reindexed = {};
    for (const [lang, arr] of Object.entries(translationsByLang)) {
      reindexed[lang] = valid.map(({ originalIndex }) => arr[originalIndex] ?? undefined);
    }
  }
  return { lines, translationsByLang: reindexed };
}
