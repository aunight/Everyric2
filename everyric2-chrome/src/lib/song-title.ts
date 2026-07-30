export interface ParsedSongTitle {
  title: string;
  artist: string | null;
}

const BRACKETED_PROMO: RegExp[] = [
  /[([]\s*(?:(?:official\s+)?(?:(?:music|lyrics?)\s+)?video|(?:official\s+)?audio|lyrics?(?:\s+video)?|m\/?v|4k|uhd|hd|hq)[^)\]]*[)\]]/gi,
  /【\s*(?:(?:official\s+)?(?:(?:music|lyrics?)\s+)?video|(?:official\s+)?audio|lyrics?(?:\s+video)?|m\/?v|4k|uhd|hd|hq)[^】]*】/gi,
];

/** "ft./feat. 이름들" 절 — 괄호 유무 모두. 검색 쿼리에 실리면 LRCLIB·넷이즈 매칭이 다
 *  빗나가므로 제목에서 걷어낸다(주 아티스트는 artist 필드에 이미 있다). */
// (?<![A-Za-z]) — "Drift" 속의 ft를 물지 않게. 키워드 뒤에는 점이나 공백을 요구하되
// "ft.莫宰羊"처럼 점 뒤 공백 없이 이름이 붙는 표기도 실측이라 \s*로 받는다.
const FEAT_CLAUSE =
  /\s*[(（[]?\s*(?<![A-Za-z])(?:featuring|feat|ft)(?:\.|(?=\s))\s*[^)\]）]*[)\]）]?/gi;

const TRAILING_PROMO =
  /\s*(?:[-–—|｜·•]\s*)?(?:(?:official\s+)?(?:(?:music|lyrics?)\s+)?videos?|(?:official\s+)?audio|lyrics?(?:\s+video)?|m\/?v)(?:\s*(?:\([^)]*\)|\[[^\]]*\]))?\s*$/i;
const TRAILING_QUALITY =
  /\s*(?:[-–—|｜·•]\s*)?(?:\(\s*)?(?:4k|uhd|full\s*hd|hd|hq|1080p|720p)(?:\s*\))?\s*$/i;
const LEADING_CJK_TITLE =
  /^(.*?)\s*(?:【\s*(.+?)\s*】|「\s*(.+?)\s*」|『\s*(.+?)\s*』|《\s*(.+?)\s*》|〈\s*(.+?)\s*〉)/u;
const ANIME_CONTEXT_BEFORE_THEME: RegExp[] = [
  /\s*『[^』]+』\s*(?=(?:op|ed)\s*(?:主題歌|テーマ)|(?:open(?:n)?ing|ending)\s+theme)/giu,
  /\s*《[^》]+》\s*(?=(?:op|ed)\s*(?:主題歌|テーマ)|(?:open(?:n)?ing|ending)\s+theme)/giu,
];
const TRAILING_ANIME_PROMO: RegExp[] = [
  /\s+(?:m\/?v\s+)?full\s+size(?:\s+ver(?:sion)?\.?)?(?:\s.*)?$/i,
  /\s+(?:op|ed)\s*(?:主題歌|テーマ)(?:\s.*)?$/iu,
  /\s+(?:open(?:n)?ing|ending)\s+theme(?:\s.*)?$/i,
];
const HAN = /\p{Script=Han}/u;
const KANA = /[\p{Script=Hiragana}\p{Script=Katakana}]/u;
const HANGUL = /\p{Script=Hangul}/u;
// 꼬리 라틴 대체 제목: 한자 제목 뒤에 붙은 영문 번역("舉刀自盡 (Back to Heaven)")을 뗀다.
// (a) 공백 없이 붙는 실제 제목("舉刀自盡Back to Heaven")도 있어 \s* 허용,
// (b) 남는 꼬리에 한자가 다시 나오면 대체 제목이 아니라 본제목의 일부일 수 있어 자르지
//     않는다("我的iPhone日記" 보호) — ft.절은 이보다 먼저 걷혔으므로 여기 걸리지 않는다.
const TRAILING_LATIN_TRANSLATION =
  /^(.+[\p{Script=Han}\d])\s*(?:[-–—|｜·•]\s*)?\(?[A-Za-z][^\p{Script=Han}]*$/u;
const ARTIST_TITLE_SEPARATOR = /\s(?:[-–—|])\s/u;

function normalizeWhitespace(value: string): string {
  return value.replace(/\s{2,}/g, ' ').trim();
}

function stripPromotionalText(raw: string): string {
  let title = normalizeWhitespace(raw);
  let previous = '';
  while (title !== previous) {
    previous = title;
    for (const pattern of BRACKETED_PROMO) title = title.replace(pattern, ' ');
    title = title.replace(FEAT_CLAUSE, ' ');
    for (const pattern of ANIME_CONTEXT_BEFORE_THEME) title = title.replace(pattern, ' ');
    for (const pattern of TRAILING_ANIME_PROMO) title = title.replace(pattern, ' ');
    title = title.replace(TRAILING_PROMO, ' ').replace(TRAILING_QUALITY, ' ');
    title = normalizeWhitespace(title).replace(/\s*[-–—|｜·•]\s*$/, '').trim();
  }
  return title;
}

function displayTitle(candidate: string): string {
  const title = normalizeWhitespace(candidate);
  if (!HAN.test(title) || KANA.test(title) || HANGUL.test(title)) return title;
  const translated = title.match(TRAILING_LATIN_TRANSLATION);
  return normalizeWhitespace(translated?.[1] ?? title);
}

export function parseSongTitle(raw: string): ParsedSongTitle {
  const leadingTitle = normalizeWhitespace(raw).match(LEADING_CJK_TITLE);
  const quotedTitle = leadingTitle?.slice(2).find(value => value?.trim());
  if (
    leadingTitle?.[1]?.trim()
    && quotedTitle
    && !ARTIST_TITLE_SEPARATOR.test(leadingTitle[1])
  ) {
    return {
      title: displayTitle(quotedTitle),
      artist: normalizeWhitespace(leadingTitle[1]),
    };
  }

  const cleaned = stripPromotionalText(raw);

  for (const separator of [' - ', ' – ', ' — ', ' | ']) {
    const index = cleaned.indexOf(separator);
    if (index > 0) {
      return {
        title: displayTitle(cleaned.slice(index + separator.length)),
        artist: normalizeWhitespace(cleaned.slice(0, index)),
      };
    }
  }

  return { title: displayTitle(cleaned), artist: null };
}
