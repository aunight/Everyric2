import type { Settings } from '../types';

export type ThemeName = 'dark' | 'light';

/**
 * 지금 적용할 테마를 정하는 **유일한** 자리.
 *
 * 판정에 쓰는 두 근거가 모두 유튜브 페이지 컨텍스트에만 있다:
 * - `location.host` — 뮤직(music.youtube.com)은 페이지 자체가 항상 다크다
 * - `document.documentElement`의 `dark` 속성 — 유튜브가 다크모드일 때 붙인다
 *
 * 그래서 **PiP 창 안에서 다시 판정하면 반드시 어긋난다.** PiP 문서에는 유튜브 페이지가
 * 없어 host는 about:blank 계열이고 `dark` 속성도 없다. PiP는 판정하지 말고, 콘텐츠
 * 스크립트가 여기서 구한 값을 받아 그대로 칠하기만 한다(PipController.setTheme).
 */
export function resolveTheme(settings: Settings): ThemeName {
  if (settings.theme !== 'auto') return settings.theme;
  if (location.host === 'music.youtube.com') return 'dark';
  return document.documentElement.hasAttribute('dark') ? 'dark' : 'light';
}
