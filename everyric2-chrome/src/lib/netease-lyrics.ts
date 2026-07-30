import type { LyricsData, LyricLine } from '../types';
import { parseLRC } from './lyrics-parser.ts';
import type { NeteaseLyric } from './netease';
import { toTraditionalText } from './traditional-lyrics.ts';
import { detectLyricLanguage } from './translation-visibility.ts';

function traditionalizeLine(line: LyricLine): void {
  line.text = toTraditionalText(line.text);
  for (const word of line.words ?? []) {
    word.word = toTraditionalText(word.word);
  }
}

export function neteaseToLyricsData(
  lyric: NeteaseLyric,
  targetLang: string,
): LyricsData | null {
  if (!lyric.lrc) return null;
  const lines = parseLRC(lyric.lrc);
  if (lines.length === 0) return null;

  if (detectLyricLanguage(lines.map(line => line.text)) === 'zh') {
    for (const line of lines) traditionalizeLine(line);
  }

  const translations = new Array<string | undefined>(lines.length);
  if (lyric.tlyric) {
    const trByTime = new Map<number, string>();
    for (const translatedLine of parseLRC(lyric.tlyric)) {
      if (translatedLine.time == null || !translatedLine.text) continue;
      trByTime.set(
        Math.round(translatedLine.time * 100),
        toTraditionalText(translatedLine.text),
      );
    }
    for (const [index, line] of lines.entries()) {
      if (line.time == null) continue;
      const translation = trByTime.get(Math.round(line.time * 100));
      if (!translation) continue;
      translations[index] = translation;
      if (targetLang === 'zh') line.translation = translation;
    }
  }

  const hasTranslation = translations.some(Boolean);
  const hasVisibleTranslation = hasTranslation && targetLang === 'zh';
  return {
    source: 'netease',
    synced: true,
    lines,
    plainText: lines.map(line => line.text).join('\n'),
    humanTranslated: hasVisibleTranslation,
    translationLang: hasVisibleTranslation ? 'zh' : undefined,
    availableLangs: hasTranslation ? ['zh'] : undefined,
    translationsByLang: hasTranslation ? { zh: translations } : undefined,
  };
}
