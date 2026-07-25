import type { ApiFailure, ApiFailureKind, ServerLogEntry, ServerStatus } from '../types';

/**
 * 서버 실패 → 사람이 읽는 상태로 옮기는 **순수 함수**들.
 *
 * fetch도 DOM도 chrome API도 건드리지 않는다 — 입력만 주면 결과가 정해진다.
 * 문구 결정이 여기 한 곳에 모여 있어야 백그라운드·콘텐츠·패널·PiP가 같은 말을 하고,
 * 상태별 화면을 코드만 읽고도 검증할 수 있다.
 */

// ── 마스킹 (타협 불가) ──────────────────────────────────────────
// API 키는 로그·화면·툴팁 어디에도 찍히면 안 된다. 서버는 키를 되돌려 주지 않지만
// (a) 사용자가 서버 URL 쿼리에 키를 넣어 둔 경우 (b) 오류 본문이 요청을 그대로 되비추는
// 경우가 있어, 실패 정보를 만드는 시점에 원본 키 문자열과 키처럼 생긴 쿼리 파라미터를
// 둘 다 지운다. 지우는 쪽이 과해도 잃는 건 진단 정보뿐이다.

const SECRET_PARAM = /([?&])(api[-_]?key|apikey|key|token|access[-_]?token|secret|auth)=[^&#]*/gi;

/** 문자열에서 실제 키 값을 통째로 지운다 (키가 짧아 오탐이 클 때는 건너뛴다) */
export function maskSecret(text: string, secret?: string): string {
  if (!secret || secret.length < 8) return text;
  return text.split(secret).join('***');
}

/** 경로/URL에서 키·토큰류 쿼리 값과 실제 키 문자열을 지운다 */
export function maskPath(path: string, secret?: string): string {
  return maskSecret(path.replace(SECRET_PARAM, '$1$2=***'), secret);
}

// ── 실패 분류 ────────────────────────────────────────────────────

export function failureKindFromStatus(status: number): ApiFailureKind {
  if (status === 401 || status === 403) return 'auth';
  if (status === 404) return 'notfound';
  if (status >= 500) return 'server';
  return 'client';
}

/**
 * 이 실패가 **서버 전체**를 못 쓴다는 근거인가.
 *
 * 404·기타 4xx는 그 엔드포인트만의 문제다 — 아직 배포 안 된 API를 떠보는 경로
 * (generate-from-caption)나 사라진 잡 조회가 여기 해당한다. 이걸로 서버를 죽었다고
 * 판정하면 멀쩡한 서버에서 생성 버튼이 잠긴다.
 */
export function affectsServerStatus(kind: ApiFailureKind): boolean {
  return kind === 'offline' || kind === 'timeout' || kind === 'auth' || kind === 'server'
    // 권한이 없으면 이 서버로 가는 **모든** 요청이 같은 이유로 막힌다 — 엔드포인트 국소
    // 문제가 아니므로 서버 전체 상태로 올린다. 그래야 첫 실패 한 번으로 배너가 뜬다.
    || kind === 'permission';
}

// ── 상태 만들기 ──────────────────────────────────────────────────

export function okStatus(at = Date.now()): ServerStatus {
  return { kind: 'ok', reason: '', at };
}

/** 아직 한 번도 확인하지 않은 상태 — 경고를 띄우지 않되, 서버가 필요한 조작은 잠근다 */
export function unknownStatus(at = Date.now()): ServerStatus {
  return { kind: 'unknown', reason: '', at };
}

/** 실패 종류별 원인 코드 한 조각 — 상태 코드가 있으면 그대로 노출한다 */
export function failureCode(failure: ApiFailure): string {
  if (failure.status !== undefined) return `HTTP ${failure.status}`;
  if (failure.kind === 'timeout') return '응답 없음(타임아웃)';
  // 권한 문제는 연결을 시도조차 하지 않았다 — '연결 실패'라고 쓰면 거짓말이 된다
  if (failure.kind === 'permission') return '호스트 권한 없음';
  return '연결 실패';
}

/** 실패 → 사용자에게 보여줄 한 줄 사유 */
export function describeFailure(failure: ApiFailure): string {
  switch (failure.kind) {
    case 'offline':
      return '서버에 연결할 수 없어요 — 서버가 꺼져 있거나 주소가 잘못됐어요';
    case 'timeout':
      return '서버가 제때 응답하지 않았어요';
    case 'auth':
      return 'API 키 인증에 실패했어요 — 설정에서 키를 확인해 주세요';
    case 'server':
      return '서버에서 오류가 났어요';
    case 'notfound':
      return '서버에 이 기능이 없어요 — 서버 버전이 낮을 수 있어요';
    case 'malformed':
      return '서버 응답을 해석할 수 없어요';
    case 'client':
      return '서버가 요청을 거절했어요';
    case 'permission':
      return '로컬 서버에 접근할 권한이 없어요 — 권한을 허용해 주세요';
  }
}

/** 실패 → 서버 상태. failure가 없으면 원인을 모르는 연결 실패로 본다. */
export function failureToStatus(failure: ApiFailure | null | undefined, at = Date.now()): ServerStatus {
  if (!failure) {
    return { kind: 'offline', reason: '서버에 연결할 수 없어요', code: '연결 실패', at };
  }
  const kind: ServerStatus['kind'] = failure.kind === 'auth'
    ? 'auth'
    : failure.kind === 'permission'
      ? 'permission'
      : failure.kind === 'offline' || failure.kind === 'timeout'
        ? 'offline'
        : 'error';
  return {
    kind,
    reason: describeFailure(failure),
    code: failureCode(failure),
    detail: failure.detail,
    at,
  };
}

/** 상태 + 원인 코드를 한 줄로 — 배너 본문과 버튼 툴팁이 같은 문장을 쓰게 한다 */
export function statusLine(status: ServerStatus): string {
  if (status.kind === 'ok') return '서버 정상';
  if (status.kind === 'unknown') return '서버 상태 확인 중…';
  return status.code ? `${status.reason} (${status.code})` : status.reason;
}

/** 서버가 필요한 버튼의 비활성 사유 툴팁 */
export function serverBlockedTip(status: ServerStatus): string {
  if (status.kind === 'unknown') return '서버 상태를 확인하는 중이에요 — 잠시 후 다시 시도해 주세요';
  const detail = status.detail ? `\n${status.detail}` : '';
  return `서버를 쓸 수 없어 잠겼어요: ${statusLine(status)}${detail}`;
}

/** 서버가 정상일 때만 서버가 필요한 조작을 허용한다 (unknown도 잠근다 — 기존 동작과 동일) */
export function serverUsable(status: ServerStatus): boolean {
  return status.kind === 'ok';
}

/**
 * 서버가 **고장났다고 확인된** 상태인가.
 *
 * `serverUsable`과 다르다: 아직 확인 전(unknown)은 "쓸 수 없다"이지만 "고장났다"는
 * 아니다. 오류 배너를 띄우거나 "가사 없음"을 서버 문제 화면으로 바꾸는 판단은 이쪽을
 * 봐야 한다 — 확인 전에 고장을 단정하면 켤 때마다 없는 오류가 깜빡인다.
 */
export function serverKnownBad(status: ServerStatus): boolean {
  return status.kind === 'offline' || status.kind === 'auth' || status.kind === 'error'
    // 권한 없음도 "확인된 못 쓰는 상태"다 — 배너를 띄우고 "가사 없음" 대신 사유를 말해야
    // 한다. 서버가 죽은 것과 달리 한 번의 허용으로 풀리므로 화면이 그 버튼까지 준다.
    || status.kind === 'permission';
}

/** 이 상태가 호스트 권한 허용으로 풀리는가 — 화면이 '권한 설정 열기'를 내놓을 조건 */
export function needsHostPermission(status: ServerStatus): boolean {
  return status.kind === 'permission';
}

// ── 로그 표시 ────────────────────────────────────────────────────

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/** epoch ms → 로컬 HH:MM:SS */
export function formatLogTime(at: number): string {
  const d = new Date(at);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** 로그 한 줄 — "18:04:21  GET /api/sync/xxx  401  0.1s  API 키가 필요해요" */
export function formatLogEntry(entry: ServerLogEntry): string {
  // 권한 문제는 요청을 보내지도 않았다 — 'no-response'로 적으면 서버가 침묵한 것처럼 읽힌다
  const status = entry.status !== undefined
    ? String(entry.status)
    : entry.kind === 'timeout' ? 'timeout'
      : entry.kind === 'permission' ? 'no-perm' : 'no-response';
  const secs = `${(entry.elapsedMs / 1000).toFixed(entry.elapsedMs < 10000 ? 1 : 0)}s`;
  const detail = entry.detail ? `  ${entry.detail}` : '';
  return `${formatLogTime(entry.at)}  ${entry.method} ${entry.path}  ${status}  ${secs}${detail}`;
}
