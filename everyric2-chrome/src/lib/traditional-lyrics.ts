import { Converter } from 'opencc-js';

import type { LyricLine, LyricsData } from '../types';
import { detectLyricLanguage } from './translation-visibility.ts';

const traditionalConverter = Converter({ from: 'cn', to: 'tw' });

export function toTraditionalText(text: string): string {
  return traditionalConverter(text);
}

function normalizeLine(
  line: LyricLine,
  sourceIsChinese: boolean,
  visibleTranslationIsChinese: boolean,
): LyricLine {
  if (!sourceIsChinese && !(line.translation && visibleTranslationIsChinese)) {
    return line;
  }

  return {
    ...line,
    text: sourceIsChinese ? toTraditionalText(line.text) : line.text,
    ...(sourceIsChinese && line.words
      ? {
          words: line.words.map(word => ({
            ...word,
            word: toTraditionalText(word.word),
          })),
        }
      : {}),
    ...(line.translation && visibleTranslationIsChinese
      ? { translation: toTraditionalText(line.translation) }
      : {}),
  };
}

/**
 * Normalize Chinese text at the shared display boundary so cached Everyric
 * results and every other source follow the same Traditional Chinese rule.
 * Japanese originals are deliberately excluded even when they contain kanji.
 */
export function normalizeTraditionalLyricsData(data: LyricsData): LyricsData {
  const sourceIsChinese =
    detectLyricLanguage(data.lines.map(line => line.text)) === 'zh';
  const visibleTranslationIsChinese = data.translationLang === 'zh';
  const lines = data.lines.map(line =>
    normalizeLine(line, sourceIsChinese, visibleTranslationIsChinese),
  );
  const translationsByLang = data.translationsByLang
    ? Object.fromEntries(
        Object.entries(data.translationsByLang).map(([language, values]) => [
          language,
          language === 'zh'
            ? values.map(value =>
                value ? toTraditionalText(value) : value,
              )
            : [...values],
        ]),
      )
    : undefined;

  return {
    ...data,
    lines,
    plainText: sourceIsChinese
      ? lines.map(line => line.text).join('\n')
      : data.plainText,
    ...(translationsByLang ? { translationsByLang } : {}),
  };
}
