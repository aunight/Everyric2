/**
 * 자체 호스팅(로컬) 서버의 호스트 권한 — 선언·판정·조회.
 *
 * **왜 optional인가.** 기본 서버는 `https://everyric.moref.co`(settings.DEFAULT_SETTINGS)이고
 * 그건 모든 사용자가 쓰므로 manifest의 `host_permissions`에 남는다. 반면 로컬 서버는
 * 자체 호스팅하는 소수만 쓰는데, 예전에는 그 두 호스트도 `host_permissions`에 있어서
 * **설치 시 전원에게** 부여되고 설치 화면 권한 목록에도 표시됐다. 쓰지 않는 사람에게까지
 * 부여되는 권한은 확장이 공개한 단일 목적에 "엄격히 필요한" 것이 아니므로
 * `optional_host_permissions`로 옮기고, 필요한 사람만 옵션 페이지에서 허용한다.
 *
 * **아래 상수는 manifest.json의 `optional_host_permissions`와 문자열까지 같아야 한다** —
 * 선언되지 않은 패턴으로 `permissions.request()`를 부르면 크롬이 조용히 거부한다.
 *
 * `chrome.permissions`는 확장 페이지와 service worker에서만 쓸 수 있다(content script 불가).
 * 그래서 이 모듈의 조회 함수는 background와 옵션 페이지에서만 호출되고, 콘텐츠 쪽은
 * 서버 상태(ServerStatus.kind === 'permission')를 통해 결과만 받아 본다.
 */

/**
 * 로컬 서버 요청이 **실제로** 향하는 패턴 — 루프백 정규화(localhost → 127.0.0.1) 뒤의 주소.
 *
 * 사용자가 `localhost`로 적어도 요청은 이 주소로 나가므로(normalizeLoopbackUrl), 실제로
 * 요청하고 허용받는 권한은 언제나 이것 하나다. 아직 서버 URL을 로컬로 바꾸지 않은 사용자가
 * 미리 허용할 때도 이 패턴을 쓴다.
 */
export const CANONICAL_LOCAL_ORIGIN = 'http://127.0.0.1:8000/*';

/**
 * manifest의 optional_host_permissions와 동일해야 하는 로컬 서버 origin 패턴.
 *
 * `localhost` 쪽은 지금 코드로는 요청되지 않는다(위 정규화 때문). 그래도 선언을 남기는
 * 이유는 둘이다: (a) 정규화 규칙이 바뀌어도 곧바로 허용할 수 있다, (b) 이 권한이 필수였던
 * 이전 버전에서 이미 허용된 상태라면 선언이 있어야 계속 보이고 철회할 수 있다.
 * 선언은 부여가 아니다 — optional 권한은 설치 시 부여되지도, 설치 화면에 표시되지도 않는다.
 */
export const LOCAL_SERVER_ORIGINS = [
  'http://localhost:8000/*',
  CANONICAL_LOCAL_ORIGIN,
] as const;

/** 패턴에서 사람이 읽는 origin으로 (`http://localhost:8000/*` → `http://localhost:8000`) */
export function originOfPattern(pattern: string): string {
  return pattern.replace(/\/\*$/, '');
}

/** URL의 origin — 파싱 실패하면 null */
export function originOfUrl(url: string): string | null {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

/**
 * 요청이 **실제로 나가는** 주소로 정규화한다 — `localhost` → `127.0.0.1`.
 *
 * Windows에서 localhost 해석이 ::1(IPv6)을 먼저 시도해 요청마다 ~2s를 태우므로
 * everyric-api가 예전부터 이렇게 바꿔 보내고 있었다(baseUrl이 이 함수를 쓴다).
 *
 * **권한 판정이 이 규칙을 공유해야 하는 이유**: 사용자가 `http://localhost:8000`을
 * 입력해도 fetch는 `http://127.0.0.1:8000`으로 나간다. 입력한 주소로 권한을 확인하면
 * "권한 있음"인데 요청은 127.0.0.1 권한이 없어 실패하는, 정확히 진단이 어긋난 조용한
 * 실패가 생긴다. 그래서 정규화 규칙을 여기 한 곳에 두고 fetch·권한 확인·옵션 페이지가
 * 모두 같은 함수를 쓴다.
 */
export function normalizeLoopbackUrl(url: string): string {
  return url.replace(/^(https?:\/\/)localhost(?=[:/]|$)/i, '$1127.0.0.1');
}

/**
 * 이 URL이 이 컴퓨터(루프백)를 가리키는가 — 권한 확인이 필요한지 가르는 1차 판정.
 *
 * 이게 false면 조회 자체를 건너뛴다(원격 기본 서버는 manifest 필수 권한으로 이미 허용됨).
 * 순수 문자열 판정이라 크롬 API를 부르지 않는다 — 사실상 모든 사용자가 이 경로다.
 */
export function isLoopbackUrl(url: string): boolean {
  const host = (() => {
    try {
      return new URL(url).hostname.toLowerCase();
    } catch {
      return '';
    }
  })();
  if (host === 'localhost' || host.endsWith('.localhost')) return true;
  if (host === '[::1]' || host === '::1') return true;
  return /^127\.\d+\.\d+\.\d+$/.test(host);
}

/**
 * 이 서버 URL로 요청하려면 있어야 하는, manifest에 선언된 optional 패턴 (없으면 null).
 *
 * 정규화 후의 주소로 찾는다 — `http://localhost:8000`과 `http://127.0.0.1:8000`은 둘 다
 * `http://127.0.0.1:8000/*` 하나를 필요로 한다. 그래서 실제로 요청·허용되는 권한은 항상
 * 하나뿐이고, `localhost` 패턴은 정규화가 바뀌거나 이전 버전에서 이미 허용된 경우를 위해
 * 선언만 남겨 둔다(선언은 부여가 아니다 — optional 권한은 설치 시 부여되지 않는다).
 */
export function declaredLocalPattern(serverUrl: string): string | null {
  const origin = originOfUrl(normalizeLoopbackUrl(serverUrl));
  if (origin === null) return null;
  return LOCAL_SERVER_ORIGINS.find(p => originOfPattern(p) === origin) ?? null;
}

/** 확장이 지금 이 패턴의 호스트 권한을 갖고 있는가 (사용자 제스처 불필요 — SW에서도 된다) */
export async function hasOriginPermission(pattern: string): Promise<boolean> {
  try {
    return await chrome.permissions.contains({ origins: [pattern] });
  } catch {
    // permissions API를 못 쓰는 컨텍스트라면 "있다"고 단정하지 않는다 — 요청은 어차피
    // 실패할 것이고, 그때 원인을 권한으로 지목하는 편이 'offline'보다 정확하다
    return false;
  }
}

/** 선언된 로컬 패턴별 현재 허용 여부 — 옵션 페이지의 상태 표시용 */
export async function localPermissionState(): Promise<{ pattern: string; granted: boolean }[]> {
  return Promise.all(
    LOCAL_SERVER_ORIGINS.map(async pattern => ({
      pattern,
      granted: await hasOriginPermission(pattern),
    })),
  );
}

/**
 * 서버 URL이 로컬일 때 허용해야 하는 패턴 하나 — 로컬이 아니면 null.
 *
 * 로컬 주소인데 선언된 패턴이 없으면(포트가 다른 경우 등) `pattern`은 null이다. 그건
 * 옵션 페이지에서 허용할 수 없고 manifest 수정 + 재빌드가 필요하다.
 */
export function localTarget(serverUrl: string): { origin: string; pattern: string | null } | null {
  if (!isLoopbackUrl(serverUrl)) return null;
  const normalized = normalizeLoopbackUrl(serverUrl);
  return {
    origin: originOfUrl(normalized) ?? normalized,
    pattern: declaredLocalPattern(serverUrl),
  };
}

/**
 * 권한 때문에 이 서버를 부를 수 없는가 — 부를 수 있으면 null, 못 부르면 사유 한 줄.
 *
 * **이 함수가 없으면 무슨 일이 벌어지나.** 권한 없이 로컬 서버로 fetch를 하면 요청이 그냥
 * 실패하고(TypeError), 그건 everyric-api.request()에서 `offline`으로 분류돼 화면에
 * "서버에 연결할 수 없어요 — 서버가 꺼져 있거나 주소가 잘못됐어요"가 뜬다. 서버는 멀쩡히
 * 돌고 있는데 사용자는 서버를 의심하며 로그를 뒤진다. 원인을 짚을 단서가 화면 어디에도
 * 없는 이 조용한 실패가 최악이라, 부르기 **전에** 막고 권한 문제라고 정확히 말한다.
 */
export async function localPermissionBlock(serverUrl: string): Promise<string | null> {
  const target = localTarget(serverUrl);
  if (target === null) return null; // 원격 기본 서버 등 — manifest 필수 권한으로 이미 허용됨

  if (target.pattern === null) {
    // 로컬이지만 선언된 주소가 아니다 (포트가 다른 경우 등) — 옵션 페이지에서 허용할 수
    // 없고 manifest 수정 + 재빌드가 필요하다. 예전에도 마찬가지였지만 그때는 아무 말도
    // 하지 않았다. 여기서 사실대로 말해 두면 사용자가 포트를 맞추거나 포기할 수 있다.
    const allowed = LOCAL_SERVER_ORIGINS.map(originOfPattern).join(', ');
    return `${target.origin}은 확장이 허용할 수 있는 로컬 서버가 아니에요 (허용 가능: ${allowed})`;
  }
  if (await hasOriginPermission(target.pattern)) return null;
  return `${target.origin} 접근 권한이 없어요 — 권한 설정에서 허용해 주세요`;
}
