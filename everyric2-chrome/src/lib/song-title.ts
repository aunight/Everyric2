export interface ParsedSongTitle {
  title: string;
  artist: string | null;
}

const BRACKETED_PROMO: RegExp[] = [
  /[([]\s*(?:(?:official\s+)?(?:(?:music|lyrics?)\s+)?video|(?:official\s+)?audio|lyrics?(?:\s+video)?|m\/?v|4k|uhd|hd|hq)[^)\]]*[)\]]/gi,
  /【\s*(?:(?:official\s+)?(?:(?:music|lyrics?)\s+)?video|(?:official\s+)?audio|lyrics?(?:\s+video)?|m\/?v|4k|uhd|hd|hq)[^】]*】/gi,
];

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
const TRAILING_LATIN_TRANSLATION =
  /^(.+[\p{Script=Han}\d])\s+(?:[-–—|｜·•]\s*)?\(?[A-Za-z].*$/u;
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
