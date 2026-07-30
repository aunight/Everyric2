const TRAILING_TOPIC = /\s+[-–—]\s+topic\s*$/i;
const SEARCH_SEPARATOR =
  /(?:^|\s+)(?:feat(?:uring)?|ft)\.?(?=\s|$)|[&,，]|\s+[-–—]\s+/i;

function normalizeWhitespace(value: string): string {
  return value.replace(/\s{2,}/g, ' ').trim();
}

/** 畫面保留合作歌手，只隱藏 YouTube 自動建立頻道的 Topic 尾碼。 */
export function artistForDisplay(raw: string | null | undefined): string {
  return normalizeWhitespace(raw ?? '').replace(TRAILING_TOPIC, '').trim();
}

/** 搜尋歌詞時只送第一位歌手，避免合作名單降低第三方來源命中率。 */
export function primaryArtistForSearch(raw: string | null | undefined): string {
  const displayArtist = artistForDisplay(raw);
  const separatorIndex = displayArtist.search(SEARCH_SEPARATOR);
  return normalizeWhitespace(
    separatorIndex < 0 ? displayArtist : displayArtist.slice(0, separatorIndex),
  );
}
