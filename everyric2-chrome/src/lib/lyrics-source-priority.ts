import type { Settings } from '../types';

export type ExternalLyricsSource = Settings['lyricsSourcePriority'];

/**
 * Everyric 서버 싱크가 없는 경우의 외부 가사 소스 순서.
 *
 * 선택한 소스만 조회하고 끝내지 않는다. 그 소스가 일시적으로 응답하지 않거나 곡을
 * 갖고 있지 않아도 나머지 소스를 계속 확인해야 기존보다 가사가 줄어들지 않는다.
 */
export function lyricsSourceOrder(
  priority: Settings['lyricsSourcePriority'],
): ExternalLyricsSource[] {
  switch (priority) {
    case 'lrclib':
      return ['lrclib', 'vocaro', 'netease'];
    case 'netease':
      return ['netease', 'vocaro', 'lrclib'];
    default:
      return ['vocaro', 'lrclib', 'netease'];
  }
}
