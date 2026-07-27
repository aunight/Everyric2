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
import { setUiLanguage, t } from './lib/i18n';
import { getSettings } from './lib/settings';

/**
 * 정적 마크업(data-i18n[-html])을 t()로 치환 — chrome.i18n의 __MSG_key__ 자동 치환은
 * manifest.json 필드에서만 동작하고 임의 HTML 본문에는 적용되지 않으므로, 이 스크립트
 * 초기화 방식이 유일한 선택지다. data-i18n-html은 <code>/<strong>/<em> 같은 인라인
 * 마크업이 섞인 문단용(내용은 전부 이 파일이 직접 쓰는 고정 문자열이라 innerHTML이 안전하다).
 */
function applyStaticI18n(): void {
  document.title = t('options.pageTitle');
  document.querySelectorAll<HTMLElement>('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    if (key) el.textContent = t(key);
  });
  document.querySelectorAll<HTMLElement>('[data-i18n-html]').forEach(el => {
    const key = el.dataset.i18nHtml;
    if (key) el.innerHTML = t(key);
  });
}

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

/** 현재 설정된 서버가 로컬인지 + 그렇다면 어떤 패턴이 필요한지.
 *  uiLanguage도 매번 다시 읽어 t()에 반영한다 — 이 페이지는 content script와 별개
 *  실행 컨텍스트라 설정 변경을 이벤트로 못 받으므로, render()가 불릴 때마다(초기 로드·
 *  버튼 클릭 후·권한 변경 이벤트) 최신값으로 맞춘다. */
async function currentTarget(): Promise<{ url: string; target: ReturnType<typeof localTarget> }> {
  const { serverUrl, uiLanguage } = await getSettings();
  setUiLanguage(uiLanguage);
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
    serverKindEl.textContent = t('options.serverKind.remote');
    serverKindEl.className = 'badge off';
    serverNoteEl.className = 'note';
    serverNoteEl.textContent = t('options.serverNote.remote');
  } else if (target.pattern === null) {
    // 로컬인데 확장이 선언하지 않은 주소 — 여기서 허용할 방법이 없다. 조용히 두면 요청이
    // 계속 실패하는데 이유를 알 수 없으므로 사실대로 적는다.
    serverKindEl.textContent = t('options.serverKind.notAllowed');
    serverKindEl.className = 'badge off';
    serverNoteEl.className = 'note warn';
    serverNoteEl.textContent = t('options.serverNote.notAllowed', [
      target.origin, LOCAL_SERVER_ORIGINS.map(originOfPattern).join(', '),
    ]);
  } else {
    const granted = states.find(s => s.pattern === target.pattern)?.granted === true;
    serverKindEl.textContent = granted ? t('options.serverKind.grantedLocal') : t('options.serverKind.needsPermLocal');
    serverKindEl.className = granted ? 'badge on' : 'badge need';
    serverNoteEl.className = 'note';
    serverNoteEl.textContent = granted
      ? t('options.serverNote.granted')
      : t('options.serverNote.needsPerm', [target.origin]);
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
      marker.textContent = t('options.currentServerMarker');
      row.append(marker);
    }

    const badge = document.createElement('span');
    badge.className = granted ? 'badge on' : 'badge off';
    badge.textContent = granted ? t('options.badgeGranted') : t('options.badgeNotGranted');
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
    ? t('options.grantButton.preemptive')
    : t('options.grantButton.normal');
  revokeBtn.hidden = pendingRevoke.length === 0;
  revokeBtn.textContent = pendingRevoke.length > 1 ? t('options.revokeButton.all') : t('options.revokeButton.one');
}

grantBtn.addEventListener('click', () => {
  const origins = pendingGrant;
  if (origins.length === 0) return;
  // 여기서 await를 끼우면 제스처가 만료된다 — 곧바로 부른다
  chrome.permissions.request({ origins })
    .then(granted => {
      setMessage(granted ? t('options.grantResult.success') : t('options.grantResult.denied'), granted ? 'plain' : 'bad');
      void render();
    })
    .catch((error: unknown) => {
      setMessage(t('options.grantResult.error', [error instanceof Error ? error.message : String(error)]), 'bad');
      void render();
    });
});

revokeBtn.addEventListener('click', () => {
  const origins = pendingRevoke;
  if (origins.length === 0) return;
  chrome.permissions.remove({ origins })
    .then(removed => {
      setMessage(removed ? t('options.revokeResult.success') : t('options.revokeResult.failure'), removed ? 'plain' : 'bad');
      void render();
    })
    .catch((error: unknown) => {
      setMessage(t('options.revokeResult.error', [error instanceof Error ? error.message : String(error)]), 'bad');
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

/** uiLanguage를 먼저 읽어 t()에 반영한 뒤 정적 마크업을 치환하고, 그다음 동적 렌더를 돈다 —
 *  순서가 바뀌면 첫 페인트에서 정적 텍스트만 구언어로 잠깐 보일 수 있다. */
async function init(): Promise<void> {
  const { uiLanguage } = await getSettings();
  setUiLanguage(uiLanguage);
  applyStaticI18n();
  await render();
}

void init();
