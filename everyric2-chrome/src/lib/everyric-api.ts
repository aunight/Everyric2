import type { ApiFailure, EveryricSyncResponse, GenerateResponse, JobStatusResponse, LineMeta, ServerLogEntry, ServerStatus, SourceAttribution, SyncListItem, TranslateResult } from '../types';
import { affectsServerStatus, failureKindFromStatus, failureToStatus, maskPath, maskSecret, okStatus } from './server-status';

export interface ServerConfig {
  serverUrl: string;
  /** 빈 문자열이면 인증 헤더를 보내지 않는다 */
  apiKey?: string;
}

/**
 * 실패 사유를 회수하기 위해 **호출부가 직접 만들어 넘기는 그릇**.
 *
 * 왜 이 모양인가: 예전 `request()`는 모든 실패를 `null` 하나로 뭉갰다. 사유를 살리려면
 * 반환 타입을 결과 유니온으로 바꾸는 게 정석이지만, 그러면 이 모듈의 모든 export와
 * background의 모든 핸들러를 동시에 고쳐야 한다. 대신 **마지막 인자에 선택적 싱크**를
 * 두었다 — 기존 시그니처(`T | null`)는 그대로라 안 넘기는 호출부는 아무 변화가 없고,
 * 사유가 필요한 호출부만 `const sink: FailureSink = {}`를 만들어 넘긴 뒤 읽는다.
 *
 * 모듈 전역에 "마지막 실패"를 담아 두는 방식은 쓰지 않았다 — 서비스워커에서 여러 탭의
 * 요청이 겹치면 남의 실패를 주워 오기 때문이다. 싱크는 호출 한 건이 소유하므로 안전하다.
 */
export interface FailureSink {
  failure?: ApiFailure;
}

// ── 최근 요청 로그 ───────────────────────────────────────────────
// 화면의 접이식 로그에 그대로 나간다. 서비스워커가 잠들면 사라지는 휘발성 버퍼로 충분하다
// (지금 뭐가 잘못됐는지 보려는 용도이지, 감사 로그가 아니다).

const LOG_MAX = 20;
const serverLog: ServerLogEntry[] = [];

function pushLog(entry: ServerLogEntry): void {
  serverLog.push(entry);
  if (serverLog.length > LOG_MAX) serverLog.splice(0, serverLog.length - LOG_MAX);
}

/** 최근 요청부터 (최신순) */
export function getServerLog(): ServerLogEntry[] {
  return serverLog.slice().reverse();
}

function baseUrl(server: ServerConfig): string {
  // Windows에서 localhost 해석이 ::1(IPv6)을 먼저 시도해 요청마다 ~2s를 태울 수 있다
  // (서버는 IPv4 0.0.0.0 바인딩) — 루프백은 127.0.0.1로 정규화해 스톨을 없앤다
  return server.serverUrl
    .replace(/^(https?:\/\/)localhost(?=[:/]|$)/i, '$1127.0.0.1')
    .replace(/\/+$/, '');
}

function buildHeaders(server: ServerConfig, extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  if (server.apiKey) headers['X-API-Key'] = server.apiKey;
  return headers;
}

/** 오류 본문에서 사람이 읽을 문구를 뽑는다 — 서버는 error·hint·detail·message를 쓴다 */
const DETAIL_MAX = 240;

async function readErrorDetail(res: Response, secret?: string): Promise<string | undefined> {
  let body: string;
  try {
    body = (await res.text()).slice(0, 2000);
  } catch {
    return undefined;
  }
  if (!body.trim()) return undefined;

  let text = body;
  try {
    const parsed = JSON.parse(body) as unknown;
    if (parsed && typeof parsed === 'object') {
      const obj = parsed as Record<string, unknown>;
      // FastAPI는 detail, 우리 핸들러는 error+hint를 쓴다 — 있는 것을 순서대로 잇는다
      const parts = ['error', 'detail', 'message', 'hint']
        .map(k => obj[k])
        .filter(v => v !== undefined && v !== null)
        .map(v => (typeof v === 'string' ? v : JSON.stringify(v)));
      if (parts.length > 0) text = parts.join(' — ');
    }
  } catch {
    /* JSON이 아니면 본문 앞부분을 그대로 (HTML 오류 페이지 등) */
  }
  const cleaned = maskSecret(text.replace(/\s+/g, ' ').trim(), secret);
  return cleaned ? cleaned.slice(0, DETAIL_MAX) : undefined;
}

/**
 * 서버 요청 한 건.
 *
 * 반환은 예전과 같은 `T | null`이지만, 실패한 이유는 더 이상 버리지 않는다 —
 * sink를 넘기면 거기에 담기고, 넘기지 않아도 최근 요청 로그에는 항상 남는다.
 */
async function request<T>(
  server: ServerConfig, path: string, init?: RequestInit, timeoutMs = 4000, sink?: FailureSink,
): Promise<T | null> {
  const started = Date.now();
  const method = init?.method ?? 'GET';
  const safePath = maskPath(path, server.apiKey);

  const fail = (kind: ApiFailure['kind'], status?: number, detail?: string): null => {
    const failure: ApiFailure = {
      kind,
      status,
      detail: detail ? maskSecret(detail, server.apiKey).slice(0, DETAIL_MAX) : undefined,
      path: safePath,
      elapsedMs: Date.now() - started,
    };
    if (sink) sink.failure = failure;
    pushLog({
      at: started, method, path: safePath, ok: false,
      status, kind, detail: failure.detail, elapsedMs: failure.elapsedMs,
    });
    return null;
  };

  let res: Response;
  try {
    res = await fetch(`${baseUrl(server)}${path}`, {
      ...init,
      headers: buildHeaders(server, init?.headers as Record<string, string> | undefined),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    // AbortSignal.timeout은 TimeoutError를 던진다 — 그 밖은 서버에 닿지 못한 것
    const timedOut = error instanceof DOMException && error.name === 'TimeoutError';
    const message = error instanceof Error ? error.message : String(error);
    return fail(timedOut ? 'timeout' : 'offline', undefined, timedOut ? `${timeoutMs}ms 초과` : message);
  }

  if (!res.ok) {
    return fail(failureKindFromStatus(res.status), res.status, await readErrorDetail(res, server.apiKey));
  }
  try {
    const value = await res.json() as T;
    pushLog({
      at: started, method, path: safePath, ok: true,
      status: res.status, elapsedMs: Date.now() - started,
    });
    return value;
  } catch {
    return fail('malformed', res.status, '응답 본문이 JSON이 아니에요');
  }
}

export function lookupSync(
  server: ServerConfig, videoId: string, sink?: FailureSink,
): Promise<EveryricSyncResponse | null> {
  return request<EveryricSyncResponse>(server, `/api/sync/${encodeURIComponent(videoId)}`, undefined, 2500, sink);
}

/** 서버는 여기서 큐 등록만 하고 즉시 job_id를 반환해야 한다 (처리 대기와 무관) */
export function generateSync(
  server: ServerConfig,
  payload: { video_id: string; lyrics: string; language?: string; line_meta?: LineMeta[]; attribution?: SourceAttribution },
  sink?: FailureSink,
): Promise<GenerateResponse | null> {
  return request<GenerateResponse>(server, '/api/sync/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, 15000, sink);
}

/** 캐시를 무시하고 싱크를 강제 재생성한다 — 큐 등록 후 즉시 job_id 반환 */
export function regenerateSync(
  server: ServerConfig,
  payload: { video_id: string; lyrics: string; line_meta?: LineMeta[]; attribution?: SourceAttribution },
  sink?: FailureSink,
): Promise<GenerateResponse | null> {
  return request<GenerateResponse>(server, '/api/sync/regenerate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...payload, force: true }),
  }, 15000, sink);
}

/** 404(잡 기록 소실 — 서버 재시작 등)는 null(일시 실패)과 구분해 gone 마커로 돌려준다.
 *  null이면 폴링이 무한 재시도하므로 사라진 잡은 명시적으로 마감시켜야 한다. */
export async function getJobStatus(
  server: ServerConfig, jobId: string, sink?: FailureSink,
): Promise<JobStatusResponse | null> {
  const probe: FailureSink = {};
  const res = await request<JobStatusResponse>(
    server, `/api/job/${encodeURIComponent(jobId)}`, undefined, 4000, probe,
  );
  if (res !== null) return res;
  // 404 = 잡 기록 소실. 폴링을 명시적으로 마감시키되, 서버가 죽은 것은 아니므로
  // 실패 사유는 호출부에 넘기지 않는다 (전역 서버 상태를 내리면 안 된다)
  if (probe.failure?.status === 404) {
    return { job_id: jobId, status: 'failed', progress: 0, gone: true };
  }
  if (sink) sink.failure = probe.failure;
  return null;
}

/** 진행 중인 전사 잡 취소 요청 — 이미 끝난 잡이면 cancelled=false와 현재 상태가 온다 */
export function cancelJob(
  server: ServerConfig, jobId: string, sink?: FailureSink,
): Promise<{ job_id: string; cancelled: boolean; status?: string } | null> {
  return request<{ job_id: string; cancelled: boolean; status?: string }>(
    server, `/api/job/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }, 4000, sink,
  );
}

/** LLM 번역은 곡 전체 기준 수십 초가 걸릴 수 있어 타임아웃을 길게 잡는다.
 *  발음표기(target=ko면 한글 독음)도 항상 함께 요청한다 — 원문이 en/ko면 서버 게이트가
 *  발음만 생략하고 번역은 정상 반환. 곡 제목/아티스트는 LLM 번역 맥락으로 쓰인다. */
export function translateLyrics(
  server: ServerConfig,
  text: string,
  targetLang: string,
  song?: { title?: string | null; artist?: string | null },
  sink?: FailureSink,
): Promise<TranslateResult | null> {
  return request<TranslateResult>(server, '/api/translate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      source_lang: 'auto',
      target_lang: targetLang,
      include_pronunciation: true,
      title: song?.title || undefined,
      artist: song?.artist || undefined,
    }),
  }, 120000, sink);
}

/** inst·커버 영상을 다른 영상의 싱크에 오프셋·배속과 함께 연결 (재등록은 upsert).
 *  rate는 원곡 대비 재생 배속(nightcore≈1.25) — 서버가 t/rate+offset으로 시간축을 사상한다. */
export function linkSync(
  server: ServerConfig,
  payload: { video_id: string; source_video_id: string; offset_sec: number; rate: number },
  sink?: FailureSink,
): Promise<Record<string, unknown> | null> {
  return request<Record<string, unknown>>(server, '/api/sync/link', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }, 4000, sink);
}

/** 이 영상의 서버 싱크 전부 삭제(초기화) — 잘못 붙여넣은 가사로 만든 싱크를 걷어내고 새로 시작 */
export function resetSync(
  server: ServerConfig, videoId: string, sink?: FailureSink,
): Promise<{ removed_syncs: number; removed_links: number } | null> {
  return request<{ removed_syncs: number; removed_links: number }>(
    server, `/api/sync/${encodeURIComponent(videoId)}`, { method: 'DELETE' }, 4000, sink,
  );
}

/** 영상별 사용자 싱크 오프셋 저장 — 다음 조회부터 user_offset으로 내려온다 */
export function saveUserOffset(
  server: ServerConfig, videoId: string, offsetSec: number, sink?: FailureSink,
): Promise<{ offset_sec: number } | null> {
  return request<{ offset_sec: number }>(server, `/api/sync/offset/${encodeURIComponent(videoId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ offset_sec: offsetSec }),
  }, 4000, sink);
}

export function unlinkSync(
  server: ServerConfig, videoId: string, sink?: FailureSink,
): Promise<{ removed: boolean } | null> {
  return request<{ removed: boolean }>(server, `/api/sync/link/${encodeURIComponent(videoId)}`, {
    method: 'DELETE',
  }, 4000, sink);
}

/** 서버에 저장된 싱크 목록 — 링크 후보 선택용 (최신순) */
export function listSyncs(
  server: ServerConfig, limit = 50, sink?: FailureSink,
): Promise<SyncListItem[] | null> {
  return request<SyncListItem[]>(server, `/api/sync/list?limit=${limit}`, undefined, 4000, sink);
}

/**
 * 서버를 쓸 수 있는지 + 못 쓴다면 왜인지.
 *
 * **`/health`만 보면 안 된다.** 서버 미들웨어는 `/api` 전체에만 X-API-Key를 요구하고
 * `/health`는 상태 점검용으로 항상 열어 둔다 — 키가 틀린 배포에서도 health는 200이다.
 * 실제로 이것 때문에 모든 /api 호출이 401인데 화면은 "가사를 찾지 못했어요"를 띄웠다.
 * 그래서 도달성(/health)과 인증(/api)을 **따로** 확인한다.
 */
export async function checkServerStatus(server: ServerConfig): Promise<ServerStatus> {
  const reach: FailureSink = {};
  let health = await request<{ status: string }>(server, '/health', undefined, 1500, reach);
  if (health === null && (reach.failure?.kind === 'offline' || reach.failure?.kind === 'timeout')) {
    // 일시적 지연(콜드 스타트 등)으로 서버를 죽었다고 오판하면 생성 버튼이 잠긴다 — 한 번 더.
    // 401·5xx는 다시 물어도 답이 같으므로 재시도하지 않는다.
    reach.failure = undefined;
    health = await request<{ status: string }>(server, '/health', undefined, 4000, reach);
  }
  if (health === null) return failureToStatus(reach.failure);

  // 서버는 살아 있다 — 이제 이 키로 /api를 쓸 수 있는지 본다 (가장 가벼운 인증 엔드포인트)
  const auth: FailureSink = {};
  const listed = await listSyncs(server, 1, auth);
  if (listed !== null) return okStatus();
  // 인증·도달 문제가 아니면(구버전 서버의 404 등) 서버 자체는 쓸 수 있다고 본다
  if (auth.failure && !affectsServerStatus(auth.failure.kind)) return okStatus();
  return failureToStatus(auth.failure);
}

/** 자막 트랙의 라인(타이밍 포함) — 서버가 yt-dlp json3를 파싱해 준다.
 *  트랙 **목록**은 서버를 부르지 않는다 — 클라이언트가 워치 페이지에서 직접 읽는다
 *  (lib/yt-captions.ts). 본문만 POT 강제로 서버 경유가 유일 경로. */
export function fetchCaptionLines(
  server: ServerConfig, videoId: string, lang: string, auto: boolean, sink?: FailureSink,
): Promise<{ lines: { start: number; end: number; text: string }[] } | null> {
  return request(
    server,
    `/api/captions/${encodeURIComponent(videoId)}/${encodeURIComponent(lang)}?auto=${auto}`,
    undefined,
    35000,
    sink,
  );
}

/**
 * 자막으로 보고 있던 곡의 싱크 생성 — 서버가 자막을 직접 읽어 가사로 쓰는 전용 경로.
 *
 * **이 엔드포인트는 아직 없을 수 있다.** 없으면(404·미배포 서버) request()가 null을
 * 돌려주므로 호출부는 기존 /api/sync/generate 경로로 폴백한다 — 그래서 여기서 404와
 * 네트워크 실패를 구분하지 않는다(둘 다 "이 경로는 못 쓴다"로 같게 취급하면 충분).
 * sink에는 사유가 담기지만, 404는 affectsServerStatus()가 걸러 서버 상태를 내리지 않는다.
 */
export function generateSyncFromCaption(
  server: ServerConfig, videoId: string, sink?: FailureSink,
): Promise<GenerateResponse | null> {
  return request<GenerateResponse>(server, '/api/sync/generate-from-caption', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ video_id: videoId }),
  }, 15000, sink);
}

export interface VocaroMatchResponse {
  found: boolean;
  slug?: string | null;
  page_url?: string | null;
  ko?: string | null;
  ja?: string | null;
}

/** 일본어 원제 등 클라이언트 독음 인덱스로 못 찾는 제목을 서버 원제 인덱스에 묻는다 */
export function vocaroMatch(
  server: ServerConfig, title: string, sink?: FailureSink,
): Promise<VocaroMatchResponse | null> {
  return request<VocaroMatchResponse>(
    server, `/api/vocaro/match?title=${encodeURIComponent(title)}`, undefined, 2500, sink,
  );
}
