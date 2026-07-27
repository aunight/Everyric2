import ko from '../../_locales/ko/messages.json';
import en from '../../_locales/en/messages.json';
import ja from '../../_locales/ja/messages.json';
import type { Settings } from '../types';

type Catalog = Record<string, { message: string }>;

/**
 * 크롬 i18n(`chrome.i18n.getMessage`)은 브라우저/OS 로케일에 고정된다 — 사용자가 확장
 * 안에서 "표시 언어"를 따로 고르게 하려면(Settings.uiLanguage) 그 언어의 카탈로그를
 * 직접 들고 있다가 조회해야 한다. `_locales/*`는 매니페스트가 chrome.i18n용으로도 쓰므로
 * (default_locale) 같은 파일을 여기서도 모듈로 임포트해 중복을 피한다.
 */
const BUNDLED: Record<'ko' | 'en' | 'ja', Catalog> = {
  ko: ko as Catalog,
  en: en as Catalog,
  ja: ja as Catalog,
};

/**
 * 현재 uiLanguage — 매 호출마다 getSettings()를 await하면 t()가 비동기가 돼 렌더 코드
 * 전체를 오염시킨다. 대신 다른 설정 소비처(예: pip.ts가 opts로 미리 해석된 값을 받아
 * 두는 방식)처럼 캐시값을 쓴다 — 호출부(overlay.ts 등)가 설정이 바뀔 때 setUiLanguage로
 * 갱신한다. 기본값 'auto'는 DEFAULT_SETTINGS.uiLanguage와 같다.
 */
let currentUiLanguage: Settings['uiLanguage'] = 'auto';

export function setUiLanguage(lang: Settings['uiLanguage']): void {
  currentUiLanguage = lang;
}

function rawMessage(key: string): string {
  // uiLanguage가 명시돼 있으면 브라우저 로케일보다 우선한다 — 사용자가 "확장은
  // 영어로 보고 싶다"고 골랐는데 chrome.i18n이 그걸 몰라서 무시하면 설정이 무의미해진다
  if (currentUiLanguage !== 'auto') {
    const msg = BUNDLED[currentUiLanguage][key]?.message;
    if (msg !== undefined) return msg;
  }
  try {
    const msg = chrome.i18n.getMessage(key);
    if (msg) return msg;
  } catch {
    /* chrome.i18n을 쓸 수 없는 컨텍스트(예: 유닛 테스트) — 폴백으로 내려간다 */
  }
  // 최후 폴백 — 카탈로그에 아직 키가 없거나(작성 누락) 어떤 경로도 값을 못 준 경우.
  // 빈 문자열 대신 ko 카탈로그값(항상 채워져 있어야 한다)까지 보고, 그마저 없으면
  // 키 자체를 보여준다 — 화면이 텅 비는 것보다 "번역 누락"이 눈에 띄는 편이 낫다
  return BUNDLED.ko[key]?.message ?? key;
}

/**
 * 번역 문자열 조회 + 치환. 크롬 고유의 named placeholder(messages.json의 `placeholders`
 * 블록) 대신 단순 위치 토큰 `$1$`, `$2$`, …를 메시지 본문에 직접 쓴다 — 카탈로그마다
 * placeholders를 따로 선언하지 않아도 되고, 번들 카탈로그 경로와 chrome.i18n 경로가
 * 완전히 같은 규칙으로 치환된다(치환은 항상 이 함수가 직접 한다).
 */
export function t(key: string, subs?: string[]): string {
  const msg = rawMessage(key);
  if (!subs || subs.length === 0) return msg;
  return msg.replace(/\$(\d+)\$/g, (whole, i: string) => subs[Number(i) - 1] ?? whole);
}
