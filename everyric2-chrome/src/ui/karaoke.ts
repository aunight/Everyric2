import type { LyricLine, PronSegment, WordSegment } from '../types';

const KANJI_RE = /[㐀-鿿]/u;

/**
 * 原文字詞時間與發音莫拉時間做最大重疊對位，只替漢字建立振假名。
 *
 * 假名原文本身已可讀，不重複標註；缺少 words 時也不猜測，讓呼叫端保留整行發音作為
 * 安全退路。回傳以原 WordSegment 物件為 key，讓既有 appendKaraokeSpans callback
 * 能直接查詢，不改動歌詞或時間資料。
 */
export function buildKanjiRubyReadings(
  line: LyricLine,
  segments: readonly PronSegment[] | undefined,
): Map<WordSegment, string> {
  const readings = new Map<WordSegment, string>();
  const words = line.words ?? [];
  if (words.length === 0 || !segments?.length) return readings;

  for (const segment of segments) {
    if (!segment.text) continue;
    let bestWord: WordSegment | undefined;
    let bestOverlap = 0;
    for (const word of words) {
      const overlap = Math.min(word.end, segment.end) - Math.max(word.start, segment.start);
      if (overlap > bestOverlap) {
        bestWord = word;
        bestOverlap = overlap;
      }
    }
    if (!bestWord || !KANJI_RE.test(bestWord.word)) continue;
    readings.set(bestWord, (readings.get(bestWord) ?? '') + segment.text);
  }
  return readings;
}

/** 在既有 karaoke word span 內建立語意化 ruby；沒有讀音時維持純文字。 */
export function appendRubyText(el: HTMLElement, text: string, reading?: string): void {
  if (!reading) {
    el.textContent = text;
    return;
  }
  const ruby = document.createElement('ruby');
  ruby.className = 'ey-ruby';
  ruby.append(text);
  const rt = document.createElement('rt');
  rt.textContent = reading;
  ruby.append(rt);
  el.append(ruby);
}

/**
 * 본문 텍스트 위에 타이밍 토큰을 위치 매핑해 카라오케 span을 구성하는 공통 헬퍼.
 *
 * 토큰을 본문에서 찾아 span으로 감싸되, **토큰 사이/앞뒤의 나머지 텍스트(공백·
 * 문장부호·매핑 실패 글자)는 인접한 토큰 span 안에 끼워 넣는다** — span 밖의
 * 텍스트 노드는 sung 색이 영원히 입혀지지 않아 흰 글자로 남는 버그가 있었다.
 * 사이 텍스트는 직전 토큰과 함께, 첫 토큰 앞 텍스트는 첫 토큰과 함께 칠해진다.
 *
 * 반환: 매핑된 토큰 수 (0이면 호출부가 폴백 표시).
 */
export function appendTimedSpans<T>(
  el: HTMLElement,
  text: string,
  tokens: readonly T[],
  tokenText: (t: T) => string,
  makeEl: (t: T) => HTMLElement,
): number {
  let pos = 0;
  let mapped = 0;
  let prevEl: HTMLElement | null = null;
  let pendingLead = '';
  for (const token of tokens) {
    const tt = tokenText(token);
    if (!tt) continue;
    const idx = text.indexOf(tt, pos);
    if (idx === -1) continue; // 표기 차이로 본문에서 못 찾는 토큰은 건너뜀
    if (idx > pos) {
      const inter = text.slice(pos, idx);
      if (prevEl) prevEl.append(inter);
      else pendingLead += inter;
    }
    const spanEl = makeEl(token);
    if (pendingLead) {
      spanEl.prepend(pendingLead);
      pendingLead = '';
    }
    el.append(spanEl);
    prevEl = spanEl;
    pos = idx + tt.length;
    mapped++;
  }
  if (mapped > 0 && pos < text.length && prevEl) {
    prevEl.append(text.slice(pos));
  }
  return mapped;
}

/**
 * line.text 본문 위에 word 토큰을 위치 매핑해 카라오케 span을 구성한다.
 *
 * 서버(CTC) 싱크의 words는 글자 단위 토큰이라, 토큰을 공백으로 이어 붙이면
 * "N e v e r"처럼 깨진다. 대신 본문에서 각 토큰의 위치를 찾아 span으로 감싼다.
 * (LRCLIB 단어 타이밍처럼 진짜 단어 토큰에도 동일하게 동작)
 */
export function appendKaraokeSpans(
  el: HTMLElement,
  line: LyricLine,
  makeWordEl: (word: WordSegment) => HTMLElement,
): void {
  const mapped = appendTimedSpans(el, line.text, line.words ?? [], w => w.word, makeWordEl);
  if (mapped > 0) return;
  // words가 없거나 표기 차이로 전 토큰 매핑 실패 — 라인이 통째로 켜지는 대신
  // 음절 타이밍(pronSegments)이나 라인 구간으로 글자에 시간을 비례 배분한다
  const synth = synthesizeCharTimings(line);
  if (synth) {
    el.replaceChildren();
    for (const s of synth) {
      const w = makeWordEl(s);
      w.classList.add('ey-word-synth'); // 디버그 모드에서 합성(비례 배분) 타이밍임을 표시
      el.append(w);
    }
    return;
  }
  el.replaceChildren(line.text);
}

/** words 매핑이 전멸한 라인용 합성 글자 타이밍 — 독음 음절 span(정확)이 있으면
 * 그 구간을, 없으면 라인 [time, endTime]을 글자 수로 비례 분할한다 */
function synthesizeCharTimings(line: LyricLine): WordSegment[] | null {
  const chars = [...line.text];
  if (chars.length === 0) return null;
  const segs = line.pronSegments;
  let t0: number | null = null;
  let t1: number | null = null;
  if (segs && segs.length > 0) {
    t0 = segs[0].start;
    t1 = segs[segs.length - 1].end;
  } else if (line.time != null && line.endTime != null) {
    t0 = line.time;
    t1 = line.endTime;
  }
  if (t0 == null || t1 == null || !(t1 > t0)) return null;
  const span = t1 - t0;
  return chars.map((ch, i) => ({
    word: ch,
    start: t0 + (span * i) / chars.length,
    end: t0 + (span * (i + 1)) / chars.length,
  }));
}
