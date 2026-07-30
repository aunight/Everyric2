import type { LyricLine, WordSegment } from '../types';

const BRACKET_PATTERNS = [
  /\([^()]*\)/gu,
  /（[^（）]*）/gu,
  /\[[^\[\]]*\]/gu,
  /【[^【】]*】/gu,
];

function bracketMask(text: string): boolean[] {
  const mask = new Array<boolean>(text.length).fill(false);
  const shadow = text.split('');
  let changed = true;

  while (changed) {
    changed = false;
    for (const pattern of BRACKET_PATTERNS) {
      pattern.lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = pattern.exec(shadow.join(''))) !== null) {
        const start = match.index;
        const end = start + match[0].length;
        for (let index = start; index < end; index += 1) {
          mask[index] = true;
          shadow[index] = ' ';
        }
        changed = true;
      }
    }
  }

  return mask;
}

function textOutsideMask(text: string, mask: readonly boolean[]): string {
  let visible = '';
  for (let index = 0; index < text.length; index += 1) {
    if (!mask[index]) visible += text[index];
  }
  return visible.replace(/\s{2,}/gu, ' ').trim();
}

export function stripScoringBrackets(text: string): string {
  return textOutsideMask(text, bracketMask(text));
}

function wordsOutsideMask(
  lineText: string,
  words: readonly WordSegment[],
  mask: readonly boolean[],
): WordSegment[] {
  let cursor = 0;
  const visibleWords: WordSegment[] = [];

  for (const word of words) {
    let visible = '';
    for (const character of word.word) {
      const index = lineText.indexOf(character, cursor);
      if (index < 0) {
        visible += character;
        continue;
      }
      const end = index + character.length;
      if (!mask.slice(index, end).some(Boolean)) visible += character;
      cursor = end;
    }
    visible = visible.replace(/\s{2,}/gu, ' ').trim();
    if (visible) visibleWords.push({ ...word, word: visible });
  }

  return visibleWords;
}

function optionalScoringText(text: string | undefined): string | undefined {
  if (!text) return text;
  const visible = stripScoringBrackets(text);
  return visible || undefined;
}

function scoringPronunciations(
  pronunciations: Record<string, string> | undefined,
): Record<string, string> | undefined {
  if (!pronunciations) return undefined;
  const visible = Object.fromEntries(
    Object.entries(pronunciations)
      .map(([script, value]) => [script, stripScoringBrackets(value)])
      .filter((entry): entry is [string, string] => Boolean(entry[1])),
  );
  return Object.keys(visible).length > 0 ? visible : undefined;
}

/**
 * Build a PiP/scoring-only lyric view. The array length and timing stay
 * identical to the engine's source data, so current-line indices never drift.
 */
export function lyricsForScoring(lines: readonly LyricLine[]): LyricLine[] {
  return lines.map(line => {
    const mask = bracketMask(line.text);
    const text = textOutsideMask(line.text, mask);

    if (!text) {
      return {
        ...line,
        text: '',
        words: [],
        notes: [],
        translation: undefined,
        pronunciation: undefined,
        pronSegments: [],
        pron: undefined,
        pronSegsByScript: undefined,
      };
    }

    const translation = optionalScoringText(line.translation);
    const pronunciation = optionalScoringText(line.pronunciation);
    const pron = scoringPronunciations(line.pron);
    const textChanged = text !== line.text;
    const translationChanged = translation !== line.translation;
    const pronunciationChanged = pronunciation !== line.pronunciation;
    const pronChanged =
      JSON.stringify(pron) !== JSON.stringify(line.pron);

    if (
      !textChanged &&
      !translationChanged &&
      !pronunciationChanged &&
      !pronChanged
    ) {
      return line;
    }

    return {
      ...line,
      text,
      ...(textChanged && line.words
        ? { words: wordsOutsideMask(line.text, line.words, mask) }
        : {}),
      translation,
      pronunciation,
      pron,
    };
  });
}
