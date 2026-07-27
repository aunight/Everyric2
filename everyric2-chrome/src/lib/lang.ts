import type { LyricLine, PronSegment, Settings } from '../types';

export type PronScript = 'hangul' | 'romaji' | 'kana';

/**
 * 발음 표기 방식 해석 — 'auto'면 번역 언어 기준 자동 결정표를 따른다
 * (공유 계약: ko→hangul, en→romaji, ja→kana, zh→hangul — zh는 아직 전용 표기가 없어
 * hangul로 폴백한다).
 */
export function resolveScript(
  settings: Pick<Settings, 'pronunciationScript' | 'translationLanguage'>,
): PronScript {
  if (settings.pronunciationScript !== 'auto') return settings.pronunciationScript;
  switch (settings.translationLanguage) {
    case 'en': return 'romaji';
    case 'ja': return 'kana';
    default: return 'hangul'; // ko·zh·그 외
  }
}

/**
 * 표시용 발음 문자열 — line.pron[script]가 있으면 그 값, 없으면 레거시 pronunciation(한글)
 * 으로 폴백한다. 표시 지점은 항상 이 함수를 거쳐야 한다(직접 line.pronunciation을 읽지
 * 않는다) — 서버가 아직 pron dict를 안 주는 동안에도 이 폴백 덕분에 오늘과 동일하게 동작한다.
 */
export function resolvedPronunciation(line: LyricLine, script: PronScript): string | undefined {
  return line.pron?.[script] ?? line.pronunciation;
}

/** 표시용 발음 음절 타이밍 — 규칙은 resolvedPronunciation과 동일(표기별 값 → 레거시 폴백) */
export function resolvedPronSegments(line: LyricLine, script: PronScript): PronSegment[] | undefined {
  return line.pronSegsByScript?.[script] ?? line.pronSegments;
}
