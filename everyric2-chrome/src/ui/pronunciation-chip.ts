import {
  resolvedPronSegments,
  resolvedPronunciation,
  type PronScript,
} from '../lib/lang.ts';
import type { LyricLine, PronSegment, Settings } from '../types';

type PronunciationPatch = Pick<Settings, 'showPronunciation'>
  & Partial<Pick<Settings, 'pronunciationScript'>>;

function isRomanizedPronunciation(value: string | undefined): value is string {
  return Boolean(
    value?.trim()
    && /[A-Za-zÀ-ÖØ-öø-ÿĀ-žǍ-ǜ]/u.test(value)
    && !/[가-힣぀-ゟ゠-ヿ]/u.test(value),
  );
}

function chinesePinyinSource(line: LyricLine): 'romaji' | 'legacy' | null {
  if (isRomanizedPronunciation(line.pron?.romaji)) return 'romaji';
  if (isRomanizedPronunciation(line.pronunciation)) return 'legacy';
  return null;
}

export function resolvedSongPronunciation(
  line: LyricLine,
  songLang: string,
  script: PronScript,
): string | undefined {
  if (songLang !== 'zh') return resolvedPronunciation(line, script);
  const source = chinesePinyinSource(line);
  return source === 'romaji'
    ? line.pron?.romaji?.trim()
    : source === 'legacy'
      ? line.pronunciation?.trim()
      : undefined;
}

export function resolvedSongPronSegments(
  line: LyricLine,
  songLang: string,
  script: PronScript,
): PronSegment[] | undefined {
  if (songLang !== 'zh') return resolvedPronSegments(line, script);
  const source = chinesePinyinSource(line);
  return source === 'romaji'
    ? line.pronSegsByScript?.romaji
    : source === 'legacy'
      ? line.pronSegments
      : undefined;
}

export function pronunciationChipKey(
  songLang: string,
  show: boolean,
  script: PronScript,
): 'off' | 'kk' | 'pinyin' | PronScript {
  if (!show) return 'off';
  if (songLang === 'en') return 'kk';
  if (songLang === 'zh') return 'pinyin';
  return script;
}

export function nextPronunciationPatch(
  songLang: string,
  show: boolean,
  script: PronScript,
): PronunciationPatch {
  if (songLang === 'zh') {
    return show
      ? { showPronunciation: false }
      : { showPronunciation: true, pronunciationScript: 'romaji' };
  }
  if (songLang === 'en') return { showPronunciation: !show };
  if (!show) return { showPronunciation: true, pronunciationScript: 'hangul' };

  const order: PronScript[] = ['hangul', 'romaji', 'kana'];
  const index = order.indexOf(script);
  return index >= order.length - 1
    ? { showPronunciation: false }
    : { showPronunciation: true, pronunciationScript: order[index + 1] };
}
