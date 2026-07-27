// 가사 소스(위키/DB)를 하나의 계약으로 묶는 인터페이스.
//
// vocaro.wikidot.com·vocaloidlyrics.miraheze.org·lrclib.net처럼 마크업도 라이선스도
// 다른 소스들이 "원문+발음+번역 줄 목록"이라는 같은 모양으로 나온다는 점만 공유한다.
// 각 소스 클라이언트(vocaro.ts·miraheze.ts 등)는 자기 응답을 이 타입으로 어댑트해서
// 반환하고, 소비처(content.ts)는 소스별 분기 없이 이 계약 하나만 보고 처리한다.
//
// 독립 타입 파일이다 — types.ts에 의존하지 않는다(순환 임포트·소유권 분리 목적).

export interface SourceLine {
  text: string;
  pronunciation?: string;
  translation?: string;
}

export interface SourceResult {
  sourceId: 'vocaro' | 'miraheze' | 'lrclib' | 'manual';
  pageUrl: string;
  pageTitle: string;
  lines: SourceLine[];
  /** 위키 발음의 표기 — 어느 script 키에 실을지 결정한다 */
  pronLang?: 'hangul' | 'romaji';
  /** 위키 번역의 언어 */
  translationLang?: 'ko' | 'en';
  /** "CC BY-SA 4.0" 등 — 출처 표기에 그대로 노출 */
  license?: string;
}
