/** 채점 기록 저장 — chrome.storage.local, 곡(videoId)별 이력 + 최고점 조회.
 *
 * 노래가 끝나거나 PiP를 닫을 때 pip.ts가 flush한 최종 점수를 한 건씩 쌓는다.
 * 서버에는 보내지 않는다 — 채점은 이 브라우저의 마이크로만 이뤄진 개인 기록이다.
 */

export interface ScoreRecord {
  videoId: string;
  title: string;
  score: number;
  at: number; // Date.now()
}

const KEY = 'scoreHistory';
/** ponytail: 상한 200건 — 넘치면 오래된 것부터 버린다. 통계 UI가 생기면 그때 늘린다. */
const MAX_RECORDS = 200;
let writeQueue: Promise<void> = Promise.resolve();

async function readAll(): Promise<ScoreRecord[]> {
  try {
    const stored = await chrome.storage.local.get(KEY);
    const list = stored[KEY];
    return Array.isArray(list) ? (list as ScoreRecord[]) : [];
  } catch {
    return [];
  }
}

/** 기록 추가 후 이 곡의 최고점을 돌려준다 (방금 점수 포함) */
export async function addScore(videoId: string, title: string, score: number): Promise<number> {
  const write = async (): Promise<number> => {
    const list = await readAll();
    list.unshift({ videoId, title, score, at: Date.now() });
    try {
      await chrome.storage.local.set({ [KEY]: list.slice(0, MAX_RECORDS) });
    } catch {
      /* 저장 실패해도 이번 세션 표시는 계속된다 */
    }
    return list
      .filter(r => r.videoId === videoId)
      .reduce((best, r) => Math.max(best, r.score), score);
  };
  const result = writeQueue.then(write, write);
  writeQueue = result.then(() => undefined, () => undefined);
  return result;
}

/** 이 곡의 최고점 — 기록이 없으면 null */
export async function bestScore(videoId: string): Promise<number | null> {
  const list = await readAll();
  const mine = list.filter(r => r.videoId === videoId);
  if (mine.length === 0) return null;
  return mine.reduce((best, r) => Math.max(best, r.score), 0);
}

/** 전체 이력 (최신순) — 이후 기록 UI용 */
export async function allScores(): Promise<ScoreRecord[]> {
  return readAll();
}
