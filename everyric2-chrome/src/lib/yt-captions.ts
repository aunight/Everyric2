import type { CaptionLine } from '../types';

/**
 * 유튜브 영상의 자막 트랙 목록 읽기 + 가사용 트랙 선택 — **유튜브 비공식 응답 구조 의존**.
 *
 * ## 왜 목록은 클라이언트가 읽고 본문은 서버가 읽나
 * 워치 페이지 HTML에는 플레이어 응답(`ytInitialPlayerResponse`)이 인라인으로 박혀 있고
 * 그 안의 `"captionTracks":[...]`에 트랙 목록이 전부 들어 있다 — 목록은 여기서 바로 읽힌다.
 * 반면 각 트랙의 `baseUrl`(timedtext)로 본문을 받으려 하면 **200 + 빈 본문**이 온다(실측).
 * 유튜브가 POT(proof-of-origin)를 요구해 브라우저 플레이어 밖에서는 자막 본문을 주지 않기
 * 때문이다. 그래서 baseUrl은 아예 이 모듈 밖으로 내보내지 않고(오용 방지), 본문은 서버
 * (`GET /api/captions/{video_id}/{lang}`)에서 받는다.
 *
 * ## 깨짐에 대한 태도
 * 유튜브가 응답 구조를 바꾸면 여기가 먼저 깨진다. lib/yt-player.ts와 같은 규약을 따른다:
 * **예외를 던지지 않고 빈 배열/ null을 돌려준다.** 자막 폴백은 "있으면 좋은" 경로이므로
 * 파싱이 실패하면 조용히 건너뛰고 기존 빈 상태 화면으로 간다.
 *
 * 이 파일에서 DOM·네트워크를 만지는 함수는 getCaptionTracks 계열뿐이고,
 * 나머지(파싱·언어 판정·시간 겹침 병합)는 전부 순수 함수다.
 */

/** 유튜브 비공식 응답 구조 의존 지점 — 개편 시 여기부터 확인 */
const CAPTION_TRACKS_KEY = '"captionTracks":';
/** 균형 스캔 상한 — 비정상 입력에서 전체 문서를 훑지 않도록 (트랙 16개도 ~8KB) */
const MAX_ARRAY_SCAN = 400_000;
/** 워치 페이지 재요청 타임아웃 — 폴백 경로라 오래 붙잡지 않는다 */
const PAGE_FETCH_TIMEOUT_MS = 8000;

export interface YtCaptionTrack {
  /** 유튜브 languageCode 원본 (예: "ja", "zh-Hans", "pt-BR") — 서버 자막 API에 그대로 넘긴다 */
  lang: string;
  /** 지역 표기를 뗀 기본 언어 (예: "zh") — 언어 비교는 항상 이 값으로 한다 */
  baseLang: string;
  /** 자동 생성(asr) 여부 */
  auto: boolean;
  /** 유튜브가 준 표시 이름 (예: "日本語", "한국어 (자동 생성)") */
  label: string;
}

// ── 파싱 (순수) ────────────────────────────────────────────────────

/**
 * `"captionTracks":[ ... ]` 배열의 끝을 찾는다.
 *
 * 정규식으로 `\[.*?\]`를 쓰면 `name.runs:[...]`처럼 **중첩 배열**이 있는 항목에서 잘린다.
 * 그래서 문자열 리터럴과 이스케이프를 존중하는 괄호 균형 스캐너로 정확히 잘라낸다.
 * 닫는 괄호를 못 찾으면(잘린 HTML 등) null.
 */
export function sliceBalancedArray(text: string, start: number): string | null {
  if (text[start] !== '[') return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  const limit = Math.min(text.length, start + MAX_ARRAY_SCAN);
  for (let i = start; i < limit; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === '[' || ch === '{') depth++;
    else if (ch === ']' || ch === '}') {
      depth--;
      if (depth === 0) return text.slice(start, i + 1);
    }
  }
  return null;
}

/** "pt-BR" → "pt". 언어 비교는 지역 표기를 떼고 한다 */
export function baseLanguage(code: string): string {
  return code.toLowerCase().split(/[-_]/)[0] || code.toLowerCase();
}

function trackLabel(name: unknown): string {
  if (typeof name !== 'object' || name === null) return '';
  const n = name as { simpleText?: unknown; runs?: unknown };
  if (typeof n.simpleText === 'string') return n.simpleText;
  if (Array.isArray(n.runs)) {
    return n.runs
      .map(r => (typeof r === 'object' && r !== null && typeof (r as { text?: unknown }).text === 'string'
        ? (r as { text: string }).text
        : ''))
      .join('');
  }
  return '';
}

/**
 * 워치 페이지 HTML(또는 플레이어 응답 스크립트 본문)에서 자막 트랙 목록을 뽑는다.
 * 자막이 없거나 구조가 바뀌었으면 예외 대신 빈 배열.
 */
export function parseCaptionTracks(source: string): YtCaptionTrack[] {
  try {
    const key = source.indexOf(CAPTION_TRACKS_KEY);
    if (key < 0) return [];
    const start = source.indexOf('[', key + CAPTION_TRACKS_KEY.length);
    if (start < 0) return [];
    const json = sliceBalancedArray(source, start);
    if (!json) return [];
    const raw: unknown = JSON.parse(json);
    if (!Array.isArray(raw)) return [];

    const tracks: YtCaptionTrack[] = [];
    const seen = new Set<string>();
    for (const entry of raw) {
      if (typeof entry !== 'object' || entry === null) continue;
      const item = entry as { languageCode?: unknown; kind?: unknown; name?: unknown };
      if (typeof item.languageCode !== 'string' || !item.languageCode) continue;
      const auto = item.kind === 'asr';
      const key2 = `${item.languageCode}|${auto}`;
      if (seen.has(key2)) continue; // 같은 (언어, 종류)가 중복되면 첫 항목만
      seen.add(key2);
      tracks.push({
        lang: item.languageCode,
        baseLang: baseLanguage(item.languageCode),
        auto,
        label: trackLabel(item.name) || item.languageCode,
      });
    }
    return tracks;
  } catch {
    return []; // 유튜브 구조 변경·잘린 HTML — 폴백을 조용히 포기한다
  }
}

// ── 언어 판정 (순수) ───────────────────────────────────────────────

/**
 * 문자 체계로 추정한 언어 후보 (우선순위 순). 확신이 없으면 빈 배열.
 * asr 트랙이 없어 원어를 알 수 없을 때의 최후 힌트로만 쓴다.
 */
export function guessLanguagesFromText(text: string): string[] {
  if (/[぀-ゟ゠-ヿ]/.test(text)) return ['ja']; // 가나 → 일본어 확정
  if (/[가-힣]/.test(text)) return ['ko'];
  if (/[Ѐ-ӿ]/.test(text)) return ['ru'];
  if (/[؀-ۿ]/.test(text)) return ['ar'];
  if (/[฀-๿]/.test(text)) return ['th'];
  if (/[ऀ-ॿ]/.test(text)) return ['hi'];
  // 한자만 있는 제목은 일본어/중국어가 모호하다 — 둘 다 후보로 두고 있는 쪽을 쓴다
  if (/[一-鿿]/.test(text)) return ['ja', 'zh'];
  return []; // 라틴 문자 등 — 문자만으로는 못 정한다
}

/**
 * 이 영상의 "노래 언어" 자막 트랙을 고른다. 못 정하면 null(폴백 포기).
 *
 * 판정 규칙과 근거:
 * 1. **asr 트랙의 언어 = 원어.** 유튜브는 영상에서 실제로 들리는 언어에 대해서만
 *    자동 생성 자막을 만든다. 번역 자막은 captionTracks에 나오지 않으므로(요청 시
 *    translationLanguages로 생성) asr은 원어의 강한 신호다.
 * 2. 원어를 알아냈으면 **같은 언어의 수동작성 트랙을 우선**한다 — asr은 가사에서
 *    특히 부정확하다(멜로디·반주 때문). 정확한 코드 일치 > 기본 언어 일치 순.
 * 3. asr이 없을 때:
 *    a. 수동작성 트랙이 하나뿐이면 그것. 업로더가 자기 영상에 단 유일한 자막은
 *       원어 가사일 가능성이 압도적이다.
 *    b. 여러 개면 곡 제목의 문자 체계로 추정해 일치하는 트랙을 쓴다.
 *    c. 그래도 못 정하면 **포기한다.** 여러 언어 중 아무거나 고르면 번역 자막을
 *       원문 가사인 양 띄우게 되고, 그건 자막을 아예 안 띄우는 것보다 나쁘다.
 */
export function selectLyricTrack(tracks: YtCaptionTrack[], title = ''): YtCaptionTrack | null {
  const manual = tracks.filter(t => !t.auto);
  const asr = tracks.find(t => t.auto);

  if (asr) {
    return manual.find(t => t.lang === asr.lang)
      ?? manual.find(t => t.baseLang === asr.baseLang)
      ?? asr;
  }
  if (manual.length === 1) return manual[0];
  if (manual.length === 0) return null;
  for (const lang of guessLanguagesFromText(title)) {
    const hit = manual.find(t => t.baseLang === lang);
    if (hit) return hit;
  }
  return null;
}

/**
 * 2단 표시용 한국어 자막 트랙 — **수동작성만**.
 *
 * 유튜브의 기계 번역 자막은 captionTracks에 들어오지 않지만(요청 시 생성),
 * 원어가 한국어인 영상의 asr 한국어는 들어온다. asr은 ASR 오차가 그대로 남으므로
 * 번역 표기로 쓰지 않고, 그런 곡은 기존 LLM 번역 경로에 맡긴다.
 * 원어 자체가 한국어면 2단이 무의미하므로 null.
 */
export function selectKoreanTrack(
  tracks: YtCaptionTrack[], chosen: YtCaptionTrack | null,
): YtCaptionTrack | null {
  if (chosen && chosen.baseLang === 'ko') return null;
  return tracks.find(t => !t.auto && t.baseLang === 'ko') ?? null;
}

/** 표시용 언어 이름 (예: "ja" → "일본어"). 실패하면 유튜브가 준 라벨 */
export function languageDisplayName(track: YtCaptionTrack): string {
  try {
    const name = new Intl.DisplayNames(['ko'], { type: 'language' }).of(track.lang);
    if (name && name !== track.lang) return name;
  } catch {
    /* Intl.DisplayNames 미지원 — 라벨로 폴백 */
  }
  return track.label || track.lang;
}

/** 출처 배지에 붙일 문구 (예: "일본어 · 업로더 자막") */
export function captionSourceLabel(track: YtCaptionTrack): string {
  return `${languageDisplayName(track)} · ${track.auto ? '자동 생성' : '업로더 자막'}`;
}

// ── 시간 겹침 병합 (순수) ──────────────────────────────────────────

/**
 * 번역 자막(한국어)을 원문 자막 라인에 **시간 구간 겹침**으로 붙인다.
 * 결과는 원문 라인과 같은 길이의 배열 — 대응이 없으면 undefined.
 *
 * 두 트랙은 라인 경계가 서로 다르다(1:다, 다:1 모두 발생):
 * - **1:다** — 번역 라인의 과반이 이 원문 라인 안에 들어오면 그 라인들을 순서대로 이어 붙인다.
 *   경계가 겹치지 않는 자막에서는 한 번역 라인의 과반이 두 원문 라인에 동시에 들어갈 수
 *   없으므로, 같은 번역이 여러 줄에 중복되지 않는다.
 * - **다:1** — 위 규칙으로 아무것도 안 붙었는데 긴 번역 라인 하나가 이 원문 라인의 과반을
 *   덮으면 그것을 쓴다(같은 번역이 이웃 줄에 함께 보일 수 있지만, 비워 두는 것보다 낫다).
 * - 둘 다 아니면 비워 둔다.
 */
export function mergeCaptionTranslation(
  base: CaptionLine[], translated: CaptionLine[],
): (string | undefined)[] {
  return base.map(b => {
    const baseDur = b.end - b.start;
    const inside: string[] = [];
    let best: { text: string; overlap: number } | null = null;

    for (const t of translated) {
      const overlap = Math.min(b.end, t.end) - Math.max(b.start, t.start);
      if (overlap <= 0) continue;
      const trDur = t.end - t.start;
      if (trDur > 0 && overlap >= trDur * 0.5) inside.push(t.text);
      if (!best || overlap > best.overlap) best = { text: t.text, overlap };
    }

    if (inside.length > 0) return inside.join(' ').trim() || undefined;
    if (best && baseDur > 0 && best.overlap >= baseDur * 0.5) return best.text.trim() || undefined;
    return undefined;
  });
}

// ── 트랙 목록 취득 (DOM·네트워크) ──────────────────────────────────

/**
 * 이 영상의 자막 트랙 목록. 실패·자막 없음은 전부 빈 배열이다(예외 없음).
 *
 * 1) 현재 문서의 인라인 플레이어 응답 스크립트를 먼저 본다 (네트워크 0).
 * 2) 없으면 워치 페이지를 다시 받아 파싱한다.
 *
 * (2)가 필요한 이유: 유튜브는 SPA라 영상을 넘겨도 **DOM의 인라인 스크립트는 처음 로드한
 * 영상 것 그대로** 남는다. 그래서 (1)에서는 스크립트 본문에 현재 videoId가 들어 있는지
 * 반드시 확인하고, 아니면 재요청으로 넘어간다.
 */
export async function getCaptionTracks(videoId: string): Promise<YtCaptionTrack[]> {
  const inline = tracksFromDocument(videoId);
  if (inline.length > 0) return inline;
  return await tracksFromWatchPage(videoId);
}

/** 현재 문서에 박힌 플레이어 응답에서 — 단, 이 영상 것이 맞을 때만 */
function tracksFromDocument(videoId: string): YtCaptionTrack[] {
  try {
    // videoDetails.videoId가 같은 스크립트 안에 있어야 이 영상의 플레이어 응답이다.
    // (ytInitialData 등 다른 스크립트에는 captionTracks가 없어 오검출 위험이 낮다)
    const stamp = `"videoId":"${videoId}"`;
    for (const script of document.querySelectorAll('script')) {
      const text = script.textContent;
      if (!text || !text.includes(CAPTION_TRACKS_KEY) || !text.includes(stamp)) continue;
      const tracks = parseCaptionTracks(text);
      if (tracks.length > 0) return tracks;
    }
  } catch {
    /* DOM 접근 실패 — 재요청 경로로 */
  }
  return [];
}

/** 워치 페이지 재요청 — location.origin으로 보내 항상 동일 출처(www/music 모두) */
async function tracksFromWatchPage(videoId: string): Promise<YtCaptionTrack[]> {
  try {
    const res = await fetch(`${location.origin}/watch?v=${encodeURIComponent(videoId)}`, {
      credentials: 'same-origin',
      signal: AbortSignal.timeout(PAGE_FETCH_TIMEOUT_MS),
    });
    if (!res.ok) return [];
    return parseCaptionTracks(await res.text());
  } catch {
    return []; // 네트워크 실패·동의 페이지 등 — 폴백을 조용히 건너뛴다
  }
}
