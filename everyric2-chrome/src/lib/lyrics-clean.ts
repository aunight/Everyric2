/**
 * 붙여넣은 가사에서 **가사가 아닌 줄**(파트 표기·주석·크레딧)을 걷어낸다.
 *
 * 왜 필요한가: 나무위키·Genius에서 복붙하면 `[Verse 1]`·`1절`·`※ 주석`·`작사: …` 같은
 * 줄이 섞여 온다. 이 줄들은 (a) 전사 정렬에서 가사인 척 시간을 먹고, (b) 「원문/독음/번역」
 * 3줄 주기를 깨뜨려 tri-line 감지 자체를 실패시킨다.
 *
 * **설계 원칙은 과소 제거다.** 잘못 지우면 가사가 사라지고, 남기면 예전과 같은 상태로
 * 돌아갈 뿐이다. 그래서:
 *   - **줄 전체가 표기일 때만** 지운다. 가사 안에 "후렴"이라는 단어가 들어간 문장
 *     ("후렴이 흘러나와")은 판정 대상이 아니다 — 키워드 매칭은 줄 전체에 앵커된다.
 *   - 가사 한 줄로도 등장할 수 있는 말(`Solo`, `랩`, `All`, `반복`)은 **괄호로 감싸였을
 *     때만** 표기로 본다.
 *   - 괄호로 감싸였다는 사실만으로는 지우지 않는다 — `(하하하)`·`(우우)` 같은 백보컬
 *     줄이 전부 날아가기 때문. 안쪽 내용이 표기 키워드여야 한다.
 *   - 일본어 인용 괄호 `「」`는 괄호 목록에서 **일부러 뺐다** — 「愛してる」처럼 한 줄
 *     전체가 인용인 가사가 흔하다.
 *   - 60자를 넘는 줄은 아무리 표기처럼 보여도 손대지 않는다.
 *   - 판정이 절반 이상의 줄을 지우려 들면(=탐지가 오작동하는 입력) 아무것도 지우지 않고
 *     그 사실을 알린다 (`bailed`).
 *
 * 판정은 순수 함수(partMarkerKind)라 입력 한 줄만 주면 그대로 검증할 수 있다.
 */

import { t } from './i18n';
import type { LyricsData } from '../types';

export type MarkerKind =
  /** ※·★로 시작하는 위키 주석 줄 */
  | 'note'
  /** 파트 표기 — [Verse 1], 【후렴】, 1절, 간주 … */
  | 'section'
  /** 반복 횟수 표기 — (x2), 2회 반복 … */
  | 'repeat'
  /** 크레딧·메타 줄 — 작사: …, 번역: … */
  | 'credit'
  /** 각주 번호 — [1], [주2] */
  | 'footnote';

export interface CleanResult {
  /** 표기·주석 줄을 걷어낸 텍스트. **빈 줄은 그대로 유지한다** — tri-line의 블록 구분이 여기 의존한다 */
  text: string;
  /** 걸러낸 줄 (원문 그대로, 앞뒤 공백만 정리) — 사용자에게 무엇이 사라졌는지 보여주기 위한 것 */
  removed: string[];
  /** 걸러낸 줄의 종류 — removed와 같은 순서 */
  kinds: MarkerKind[];
  /** 너무 많이 지우게 돼 필터를 포기했다 (text는 원문 그대로) */
  bailed: boolean;
}

// ── 괄호 ────────────────────────────────────────────────────────
// 「」는 의도적으로 제외 (일본어 가사의 인용 줄). 《》·〈〉도 인용 관례가 있어 〈〉만 둔다.
const BRACKET_PAIRS: [string, string][] = [
  ['[', ']'], ['(', ')'], ['{', '}'], ['<', '>'],
  ['【', '】'], ['〔', '〕'], ['［', '］'], ['（', '）'], ['〈', '〉'],
];

/** 줄 양 끝의 대칭 괄호 한 겹을 벗긴다 — 한 겹으로 감싸인 꼴이 아니면 null */
function unwrapBrackets(s: string): string | null {
  for (const [open, close] of BRACKET_PAIRS) {
    if (!s.startsWith(open) || !s.endsWith(close) || s.length <= open.length + close.length) continue;
    const inner = s.slice(open.length, s.length - close.length);
    // 안쪽에 같은 닫는 괄호가 또 있으면 한 겹이 아니다 — "(가) 나(다)"처럼 괄호가 여러 번
    // 등장하는 가사 줄을 통째로 표기로 오인하지 않도록 손대지 않는다
    if (inner.includes(close)) return null;
    return inner.trim();
  }
  return null;
}

// ── 키워드 ──────────────────────────────────────────────────────

/** 줄 전체가 이것뿐이면 가사로 볼 수 없는 섹션 이름 (괄호가 없어도 제거) */
const SECTION_STRONG = [
  'intro', 'outro', 'verse', 'chorus', 'pre-?chorus', 'post-?chorus',
  'refrain', 'bridge', 'hook', 'interlude', 'instrumental', 'breakdown',
  'lyrics', 'chorus\\s*only',
  '인트로', '아웃트로', '아웃로', '도입부', '전주', '간주', '간주중', '후주', '엔딩',
  '후렴', '후렴구', '벌스', '코러스', '브릿지', '브리지', '훅', '가사',
  // 일본어 표기 — 보카로 가사는 원문이 일본어라 위키 복붙에 그대로 섞여 온다
  '歌詞', '間奏', '前奏', '後奏', 'イントロ', 'アウトロ', 'サビ', '大サビ', '[ABC]メロ',
];

/**
 * 괄호로 감싸였을 때만 표기로 보는 약한 신호.
 * 근거: 이 말들은 가사 한 줄로도 성립한다 — "Solo"(Jennie), "All", "랩", "반복".
 * 그래서 `(Solo)`·`[랩]`은 지우지만 `Solo` 한 줄은 남긴다.
 */
const SECTION_WEAK = [
  'solo', 'guitar\\s*solo', 'drop', 'break', 'rap', 'vocals?',
  'ad-?libs?', 'all', 'both', 'repeat', 'sample', 'skit', 'spoken',
  '솔로', '기타\\s*솔로', '랩', '보컬', '합창', '나레이션', '내레이션', '독백',
  'ラップ', '合唱', '台詞', 'セリフ',
];

/** 파트 번호로 붙을 수 있는 꼬리·머리 — "Verse 2", "2nd Chorus", "후렴 3" */
const NUM = String.raw`(?:\d{1,2}(?:st|nd|rd|th)?|[ivx]{1,4}|first|second|third|fourth|final|last|마지막|첫)`;

function sectionRegex(keys: string[]): RegExp {
  return new RegExp(`^(?:${NUM}\\s*[.\\-]?\\s*)?(?:${keys.join('|')})(?:\\s*[.\\-]?\\s*${NUM})?$`, 'i');
}

const RE_SECTION_STRONG = sectionRegex(SECTION_STRONG);
const RE_SECTION_WEAK = sectionRegex(SECTION_WEAK);
/** "1절", "제 2 절" — 절은 숫자가 붙은 꼴만 표기로 본다 */
const RE_JEOL = /^제?\s*\d{1,2}\s*절$/;
/** 각주 번호 (괄호 안일 때만) */
const RE_FOOTNOTE = /^(?:\d{1,3}|주\s*\d{0,3}|각주|note\s*\d{0,3})$/i;
/** 반복 횟수 — 숫자가 있어야 괄호 없이도 인정한다 */
const RE_REPEAT_NUM = /^(?:[x×*]\s*\d{1,2}|\d{1,2}\s*(?:회|번)(?:\s*반복)?|반복\s*[x×*]?\s*\d{1,2})$/i;
const CREDIT_ROLE =
  '(?:'
  // 한국어 메타·제작진
  + '번역|해석|의역|출처|가사\\s*출처|원문|원곡|원제|작사|작곡|편곡|노래|보컬|가창|'
  + '코러스|조교|믹스|믹싱|마스터링|녹음|프로듀서|프로듀싱|영상|동영상|일러스트|'
  + '제작|기획|감독|연주|기타|베이스|드럼|피아노|발음|독음|로마자|앨범|발매일?|재생시간|'
  // 일본어 제작진
  + '作詞|作曲|編曲|作編曲|補作|唄|歌|歌唱|ボーカル|コーラス|演奏|ミックス|'
  + 'ミキシング|マスタリング|録音|音響|制作|製作|企画|監督|プロデュース|'
  + 'プロデューサー|ギター|ベース|ドラムス?|ピアノ|ストリングス|絵|イラスト|動画|'
  + '映像|調[声整]|訳|翻訳|原曲|本家|'
  // 中文製作人員（繁簡並列）
  + '作詞|作词|作曲|編曲|编曲|作編曲|作编曲|混音(?:師|师)?|母帶(?:處理)?|母带(?:处理)?|'
  + '錄音|录音|製作(?:人)?|制作(?:人)?|監製|监制|演唱|主唱|和聲|和声|吉他|貝斯|贝斯|'
  + '鼓|鋼琴|钢琴|弦樂|弦乐|企劃|企划|導演|导演|插畫|插画|動畫|动画|影像|'
  // 영어 제작진·메타
  + 'lyrics?(?:\\s*by)?|music(?:\\s*by)?|composer|composed\\s*by|composition|lyricist|'
  + 'arrange|arranger|arrangement|arranged\\s*by|vocals?(?:\\s*by)?|singer|sung\\s*by|'
  + 'chorus|mix|mixed\\s*by|mixing|mastering|mastered\\s*by|recording|recorded\\s*by|'
  + 'producer|produced\\s*by|production|director|directed\\s*by|guitar|bass|drums?|piano|'
  + 'strings|translat(?:ion|ed\\s*by)|romaji|kanji|english|illust(?:ration|rator)?|artwork|'
  + 'animation|movie|video'
  + ')';

/** 콜론 앞이 전부 제작 역할일 때만 크레딧으로 본다 — `君の声：僕の歌`는 통과한다. */
const RE_CREDIT_HEAD = new RegExp(
  `^${CREDIT_ROLE}(?:\\s*(?:&|＆|/|／|、|・|·|\\+|＋)\\s*${CREDIT_ROLE}){0,3}$`,
  'iu',
);

/** `編曲・姓名`, `Arrangement / Name`처럼 콜론 없이 역할과 이름을 잇는 구분자. */
const RE_CREDIT_VALUE_SEPARATOR = /[・·]|\s+(?:[/／]|[-–—])\s+/gu;

/** 제작진·메타데이터 한 줄인가. 역할 라벨과 이름을 잇는 명시적 구분자가 반드시 있어야 한다. */
export function isProductionCreditLine(rawLine: string): boolean {
  const line = rawLine.replace(/\s+/g, ' ').trim();
  if (!line || line.length > 180) return false;
  const core = unwrapBrackets(line) ?? line;

  const colon = core.match(/^([^:：]{1,48})[:：]\s*(\S.*)$/u);
  if (colon && RE_CREDIT_HEAD.test(colon[1].trim())) return true;

  for (const match of core.matchAll(RE_CREDIT_VALUE_SEPARATOR)) {
    const index = match.index;
    if (index === undefined) continue;
    const head = core.slice(0, index).trim();
    const tail = core.slice(index + match[0].length).trim();
    if (tail && RE_CREDIT_HEAD.test(head)) return true;
  }
  return false;
}

/** 가사가 아닌 줄인가 — 그렇다면 어떤 종류인지, 아니면 null */
export function partMarkerKind(rawLine: string): MarkerKind | null {
  const line = rawLine.replace(/\s+/g, ' ').trim();
  if (!line) return null; // 빈 줄은 블록 구분(구조)이라 판정 대상이 아니다
  if (isProductionCreditLine(line)) return 'credit';
  // 긴 줄은 표기가 아니라 가사다 — 문장 안에 우연히 키워드가 있어도 여기서 걸러진다
  if (line.length > 60) return null;

  // ※·★로 시작하는 줄은 위키 주석 관례 — 뒤에 무슨 말이 오든 가사가 아니다
  if (/^[※☆★]/.test(line)) return 'note';

  const inner = unwrapBrackets(line);
  const bracketed = inner !== null;
  // 구분선·머리표·꼬리 기호를 걷어낸다 — "- 후렴 -", "· 간주 :", "1절)" 꼴을 같게 만든다
  const core = (inner ?? line)
    .replace(/^[-–—*·•~#=]+\s*/, '')
    .replace(/\s*[-–—*·•~:：.)=]+$/, '')
    .trim();
  if (!core) return bracketed ? 'section' : null; // "[---]" 류 구분선 (괄호 없는 "---"는 남긴다)

  if (bracketed && RE_FOOTNOTE.test(core)) return 'footnote';
  if (RE_REPEAT_NUM.test(core)) return 'repeat';
  if (bracketed && /^반복$/.test(core)) return 'repeat';

  // Genius 관례 `[Verse 2: A, B]` — 콜론 앞의 파트 이름만 본다
  const head = core.split(/[:：]/)[0].trim();
  if (RE_SECTION_STRONG.test(head)) return 'section';
  if (RE_JEOL.test(head)) return 'section';
  if (bracketed && RE_SECTION_WEAK.test(head)) return 'section';

  return null;
}

/** 판정이 참인 줄만 걸러낸 결과 — 빈 줄 구조는 보존한다 */
export function isPartMarkerLine(rawLine: string): boolean {
  return partMarkerKind(rawLine) !== null;
}

export function stripPartMarkers(raw: string): CleanResult {
  const removed: string[] = [];
  const kinds: MarkerKind[] = [];
  const kept: string[] = [];
  let keptNonEmpty = 0;

  for (const line of raw.split('\n')) {
    const kind = partMarkerKind(line);
    if (kind !== null) {
      removed.push(line.trim());
      kinds.push(kind);
      continue;
    }
    kept.push(line);
    if (line.trim()) keptNonEmpty++;
  }

  // 안전판: 절반 이상을 지우게 됐다면 판정이 이 입력에서 오작동하는 것이다. 가사를 잃는
  // 대신 아무것도 지우지 않고 알린다 (예전과 같은 동작으로 되돌아가는 것이 최악이 아니다)
  const total = keptNonEmpty + removed.length;
  if (removed.length > 0 && (keptNonEmpty === 0 || removed.length * 2 > total)) {
    return { text: raw, removed, kinds, bailed: true };
  }
  return { text: kept.join('\n'), removed, kinds, bailed: false };
}

/**
 * 서버 싱크·외부 LRC·위키·유튜브 자막 등 이미 구조화된 모든 가사에서 제작진 줄을 제거한다.
 *
 * 줄만 거르면 언어별 번역 배열의 인덱스가 어긋나므로 같은 인덱스를 함께 제거한다.
 * 변경이 없으면 원본 객체를 돌려줘 같은 곡 재적용 판정과 캐시 서명을 불필요하게 흔들지 않는다.
 */
export function stripProductionCredits(data: LyricsData): LyricsData {
  const keptIndices = data.lines
    .map((line, index) => ({ index, keep: !isProductionCreditLine(line.text) }))
    .filter(item => item.keep)
    .map(item => item.index);
  if (keptIndices.length === data.lines.length) return data;

  const lines = keptIndices.map(index => data.lines[index]);
  const translationsByLang = data.translationsByLang
    ? Object.fromEntries(
      Object.entries(data.translationsByLang)
        .filter(([, values]) => values.length === data.lines.length)
        .map(([lang, values]) => [lang, keptIndices.map(index => values[index])]),
    )
    : undefined;

  return {
    ...data,
    lines,
    plainText: lines.map(line => line.text).join('\n'),
    translationsByLang,
  };
}

/**
 * 걸러낸 결과를 사용자에게 알릴 한 줄 — 지운 게 없으면 null.
 * 조용히 지우면 "가사가 사라졌다"로 보이므로 **몇 줄을, 무엇을** 지웠는지 함께 보여준다.
 * (예전엔 한국어 하드코딩이라 zh/en/ja UI에 한국어가 샜다 — uiLanguage 카탈로그를 탄다)
 */
export function describeRemoved(res: CleanResult): string | null {
  if (res.removed.length === 0) return null;
  const sample = res.removed.slice(0, 3).join(', ');
  const more = res.removed.length > 3
    ? t('lyricsClean.moreLines', [String(res.removed.length - 3)])
    : '';
  if (res.bailed) {
    return t('lyricsClean.bailed', [String(res.removed.length)]);
  }
  const kinds = [...new Set(res.kinds)]
    .map(k => t(`lyricsClean.kind.${k}`))
    .join('·');
  return t('lyricsClean.removed', [kinds, String(res.removed.length), sample + more]);
}
