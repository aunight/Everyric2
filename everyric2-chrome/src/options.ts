/**
 * 권한 관리 페이지 (options_ui).
 *
 * **왜 이 페이지가 존재하나.** 로컬(자체 호스팅) 서버 호스트 권한을
 * `optional_host_permissions`로 옮기면 누군가는 그것을 허용할 수 있어야 한다. 그런데
 * `chrome.permissions.request()`는
 *   - content script에서 **아예 쓸 수 없고**(chrome.permissions가 없다),
 *   - service worker에서 부르면 사용자 제스처 컨텍스트가 없어 **실패한다**.
 * 확장 페이지의 버튼 클릭만이 유일하게 성립하는 조합이라, 이 확장에 처음으로 확장 페이지를
 * 만들었다.
 *
 * **범위는 권한뿐이다.** 서버 URL 입력은 가사 패널 설정에만 둔다 — 같은 값을 두 화면에서
 * 편집하게 만들면 반드시 어긋나고, 어긋난 쪽을 사용자가 알 방법이 없다.
 */

import {
  CANONICAL_LOCAL_ORIGIN,
  LOCAL_SERVER_ORIGINS,
  hasOriginPermission,
  localTarget,
  originOfPattern,
} from './lib/host-permissions';
import { getSettings } from './lib/settings';

const rowsEl = must<HTMLDivElement>('perm-rows');
const grantBtn = must<HTMLButtonElement>('grant');
const revokeBtn = must<HTMLButtonElement>('revoke');
const recheckBtn = must<HTMLButtonElement>('recheck');
const messageEl = must<HTMLDivElement>('message');
const serverUrlEl = must<HTMLElement>('server-url');
const serverKindEl = must<HTMLSpanElement>('server-kind');
const serverNoteEl = must<HTMLParagraphElement>('server-note');

function must<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (el === null) throw new Error(`options.html에 #${id}가 없다`);
  return el as T;
}

/**
 * 버튼이 요청·철회할 패턴 — **렌더 시점에 미리 계산해 둔다.**
 *
 * 클릭 핸들러 안에서 `await`로 이걸 구하면 그 사이에 사용자 제스처가 만료돼
 * `permissions.request()`가 조용히 거부될 수 있다. 그래서 클릭은 계산 없이 곧바로 호출만 한다.
 */
let pendingGrant: string[] = [];
let pendingRevoke: string[] = [];

function setMessage(text: string, tone: 'plain' | 'bad' = 'plain'): void {
  messageEl.textContent = text;
  messageEl.className = tone === 'bad' ? 'bad' : '';
}

/** 현재 설정된 서버가 로컬인지 + 그렇다면 어떤 패턴이 필요한지 */
async function currentTarget(): Promise<{ url: string; target: ReturnType<typeof localTarget> }> {
  const { serverUrl } = await getSettings();
  return { url: serverUrl, target: localTarget(serverUrl) };
}

async function render(): Promise<void> {
  const [{ url, target }, states] = await Promise.all([
    currentTarget(),
    Promise.all(LOCAL_SERVER_ORIGINS.map(async pattern => ({
      pattern,
      granted: await hasOriginPermission(pattern),
    }))),
  ]);

  // ── 현재 서버 ──────────────────────────────────────────────────
  serverUrlEl.textContent = url;
  if (target === null) {
    serverKindEl.textContent = '원격 서버';
    serverKindEl.className = 'badge off';
    serverNoteEl.className = 'note';
    serverNoteEl.textContent = '지금 설정은 원격 서버라 이 페이지의 권한이 필요하지 않아요. '
      + '서버 주소는 유튜브 가사 패널의 설정(⚙)에서 바꿉니다.';
  } else if (target.pattern === null) {
    // 로컬인데 확장이 선언하지 않은 주소 — 여기서 허용할 방법이 없다. 조용히 두면 요청이
    // 계속 실패하는데 이유를 알 수 없으므로 사실대로 적는다.
    serverKindEl.textContent = '허용 불가';
    serverKindEl.className = 'badge off';
    serverNoteEl.className = 'note warn';
    serverNoteEl.textContent = `${target.origin}은 확장이 허용할 수 있는 주소가 아니에요. `
      + `허용 가능한 로컬 주소는 ${LOCAL_SERVER_ORIGINS.map(originOfPattern).join(', ')} 뿐이고, `
      + '다른 포트나 주소를 쓰려면 확장을 다시 빌드해야 해요.';
  } else {
    const granted = states.find(s => s.pattern === target.pattern)?.granted === true;
    serverKindEl.textContent = granted ? '로컬 서버 · 허용됨' : '로컬 서버 · 권한 필요';
    serverKindEl.className = granted ? 'badge on' : 'badge need';
    serverNoteEl.className = 'note';
    serverNoteEl.textContent = granted
      ? '이 주소로 요청을 보낼 수 있어요.'
      : `아래에서 허용해야 이 주소로 요청을 보낼 수 있어요. (요청은 ${target.origin}으로 나가요)`;
  }

  // ── 권한 목록 ──────────────────────────────────────────────────
  rowsEl.replaceChildren(...states.map(({ pattern, granted }) => {
    const row = document.createElement('div');
    row.className = 'row';

    const origin = document.createElement('span');
    origin.className = 'origin grow';
    origin.textContent = originOfPattern(pattern);
    row.append(origin);

    if (target?.pattern === pattern) {
      const marker = document.createElement('span');
      marker.className = 'badge need';
      marker.textContent = '지금 설정된 서버';
      row.append(marker);
    }

    const badge = document.createElement('span');
    badge.className = granted ? 'badge on' : 'badge off';
    badge.textContent = granted ? '허용됨' : '허용되지 않음';
    row.append(badge);
    return row;
  }));

  // ── 버튼 ───────────────────────────────────────────────────────
  // 허용은 **필요한 것 하나만** 요청한다. 두 주소를 한꺼번에 받아 두면 편하지만, 실제로
  // 요청이 나가지 않는 주소까지 부여받는 것은 이 작업의 취지(엄격히 필요한 권한만)에 어긋난다.
  const needed = target?.pattern ?? CANONICAL_LOCAL_ORIGIN;
  const neededGranted = states.find(s => s.pattern === needed)?.granted === true;
  pendingGrant = target?.pattern === null || neededGranted ? [] : [needed];
  pendingRevoke = states.filter(s => s.granted).map(s => s.pattern);

  grantBtn.hidden = pendingGrant.length === 0;
  grantBtn.textContent = target === null
    ? '로컬 서버 접근 미리 허용'
    : '로컬 서버 접근 허용';
  revokeBtn.hidden = pendingRevoke.length === 0;
  revokeBtn.textContent = pendingRevoke.length > 1 ? '권한 전부 철회' : '권한 철회';
}

grantBtn.addEventListener('click', () => {
  const origins = pendingGrant;
  if (origins.length === 0) return;
  // 여기서 await를 끼우면 제스처가 만료된다 — 곧바로 부른다
  chrome.permissions.request({ origins })
    .then(granted => {
      setMessage(granted
        ? '허용됐어요. 열려 있는 유튜브 탭의 가사 패널이 자동으로 다시 확인해요.'
        : '허용되지 않았어요. 자체 호스팅 서버를 쓰려면 이 권한이 필요하고, '
          + '기본 서버(everyric.moref.co)를 쓰면 권한 없이 그대로 쓸 수 있어요.',
        granted ? 'plain' : 'bad');
      void render();
    })
    .catch((error: unknown) => {
      setMessage(`권한을 요청하지 못했어요 — ${error instanceof Error ? error.message : String(error)}`, 'bad');
      void render();
    });
});

revokeBtn.addEventListener('click', () => {
  const origins = pendingRevoke;
  if (origins.length === 0) return;
  chrome.permissions.remove({ origins })
    .then(removed => {
      setMessage(removed
        ? '철회했어요. 로컬 서버로는 더 이상 요청을 보내지 않아요 — 가사 패널에는 권한이 필요하다는 안내가 다시 떠요.'
        : '철회하지 못했어요.', removed ? 'plain' : 'bad');
      void render();
    })
    .catch((error: unknown) => {
      setMessage(`철회하지 못했어요 — ${error instanceof Error ? error.message : String(error)}`, 'bad');
      void render();
    });
});

recheckBtn.addEventListener('click', () => {
  setMessage('');
  void render();
});

// chrome://extensions에서 사이트 접근을 바꾸면 이 페이지가 열려 있어도 알림이 온다 —
// 화면에 실제 상태와 다른 것이 남지 않게 다시 그린다
chrome.permissions.onAdded.addListener(() => void render());
chrome.permissions.onRemoved.addListener(() => void render());

void render();
