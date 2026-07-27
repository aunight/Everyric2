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
 *
 * 레거시 폴백은 **script === 'hangul'일 때만** 쓴다 — line.pronunciation은 항상 한글 값이라,
 * romaji·kana를 보는 사용자(en·ja 유저)에게 그대로 돌려주면 자기 표기가 아닌 한글 독음이
 * 뜬다(ja 유저 감사에서 실측 — pron dict에 kana 키가 없는 곡마다 한글이 새어나왔다).
 * hangul 외 표기에서 dict에 값이 없으면 undefined를 그대로 돌려줘 발음 줄 자체를 생략한다.
 */
export function resolvedPronunciation(line: LyricLine, script: PronScript): string | undefined {
  return line.pron?.[script] ?? (script === 'hangul' ? line.pronunciation : undefined);
}

/** 표시용 발음 음절 타이밍 — 규칙은 resolvedPronunciation과 동일(표기별 값 → hangul만 레거시 폴백) */
export function resolvedPronSegments(line: LyricLine, script: PronScript): PronSegment[] | undefined {
  return line.pronSegsByScript?.[script] ?? (script === 'hangul' ? line.pronSegments : undefined);
}
