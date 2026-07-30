import type { LyricLine } from '../types';

export type LyricLanguage = 'zh' | 'ja' | 'ko' | 'en' | 'unknown';

type TranslationLine = Pick<LyricLine, 'text' | 'translation'>;

/**
 * 顯示層用的全曲語言判斷。
 *
 * 日文必須先看假名，否則只有漢字的行會被當成中文；其餘語系則以整首的文字量判斷，
 * 避免中英混寫時少數拉丁字母蓋過中文。這個函式不取代對齊引擎的 script 判斷。
 */
export function detectLyricLanguage(texts: readonly string[]): LyricLanguage {
  const text = texts.join('');
  const kana = text.match(/[぀-ゟ゠-ヿ]/g)?.length ?? 0;
  const hangul = text.match(/[가-힣]/g)?.length ?? 0;
  const han = text.match(/[㐀-鿿]/g)?.length ?? 0;
  const latin = text.match(/[A-Za-z]/g)?.length ?? 0;

  if (kana > 0) return 'ja';
  if (hangul >= 2 && hangul >= han) return 'ko';
  if (han > 0) return 'zh';
  if (hangul > 0) return 'ko';
  if (latin > 0) return 'en';
  return 'unknown';
}

/** 原文語言與要求的翻譯語言相同時，不必建立翻譯工作。 */
export function isSameLanguageTarget(texts: readonly string[], target: string): boolean {
  const source = detectLyricLanguage(texts);
  return source !== 'unknown' && source === target;
}

function comparableText(text: string): string {
  return text.normalize('NFKC').toLocaleLowerCase().replace(/[\p{P}\p{S}\s]+/gu, '');
}

/**
 * 回傳每行真正需要顯示的翻譯；只抑制畫面，不修改 line.translation。
 *
 * 全曲原文與翻譯同語言時全部隱藏。若全曲語言不同，仍去掉正規化後與原文完全相同的
 * 單行，避免外部資料只複製原文時在畫面上重複一次。
 */
export function visibleTranslations(
  lines: readonly TranslationLine[],
): (string | undefined)[] {
  const translations = lines
    .map(line => line.translation?.trim())
    .filter((text): text is string => Boolean(text));
  const sourceLanguage = detectLyricLanguage(lines.map(line => line.text));
  const translationLanguage = detectLyricLanguage(translations);
  const hideSong = sourceLanguage !== 'unknown' && sourceLanguage === translationLanguage;

  return lines.map(line => {
    const translation = line.translation?.trim();
    if (!translation || hideSong) return undefined;
    const source = comparableText(line.text);
    const translated = comparableText(translation);
    return source && source === translated ? undefined : translation;
  });
}
