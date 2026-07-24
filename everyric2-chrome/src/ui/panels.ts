import type { LyricLine, SearchCandidate, ServerLogEntry, ServerStatus, SongInfo } from '../types';
import { formatLogEntry, serverBlockedTip, serverKnownBad, serverUsable, statusLine } from '../lib/server-status';
import { h, icon, ICONS } from './dom';

/**
 * 가사창 UI 조각들의 공용 모듈.
 *
 * 메인 패널(LyricsOverlay)과 PiP 창이 **같은 UI를 동시에** 띄울 수 있어야 하는데,
 * 예전에는 이 조각들이 `this.body`/`this.callbacks`/`this.searchResultsEl` 같은
 * LyricsOverlay 인스턴스 필드를 직접 만졌다 — 필드가 하나뿐이라 나중에 그린 쪽이
 * 이전 참조를 덮어써 두 창에 동시에 띄울 수 없었다.
 *
 * 그래서 karaoke.ts의 appendTimedSpans/appendKaraokeSpans와 같은 규약으로 뽑았다:
 * **"콜백과 상태를 받아 엘리먼트를 만들고, 나중에 갱신할 참조만 돌려준다."**
 * 호스트(패널·PiP)는 돌려받은 참조를 자기 필드에 보관한다.
 *
 * h()가 전역 document.createElement를 쓰지만 PiP에서도 안전하다 — 다른 document의
 * 노드를 appendChild하면 브라우저가 자동으로 adopt한다 (pip.ts가 이미 그렇게 동작 중).
 */

/** 패널 조각들이 호스트에 되돌려 보내는 동작 — 패널·PiP 모두 content.ts의 같은 핸들러로 배선된다 */
export interface PanelCallbacks {
  /** 가사 텍스트로 싱크 생성. attribution은 사용자가 적어 넣은 출처(선택) */
  onGenerate: (lyrics: string, attribution?: string) => void;
  /** 자동 검색 다시 실행 — query가 없으면 자동 인식으로 되돌린다 */
  onRetrySearch: (query?: { title: string; artist: string }) => void;
  /** 후보 리스트 요청 — 결과는 호스트가 renderCandidateList로 받아 그린다 */
  onCandidateSearch: (query: { title: string; artist: string }) => void;
  /** 후보 리스트에서 사용자가 직접 선택 */
  onPickCandidate: (candidate: SearchCandidate) => void;
  /** 상세 검색 시트 열기 (호스트가 자기 창 안에서 연다) */
  onOpenSearch: () => void;
  /** 설정 열기 — 인증 실패 배너에서 API 키 칸으로 바로 보내기 위한 것 */
  onOpenSettings: () => void;
  /** 서버 상태 다시 확인 (배너의 '다시 확인') */
  onRecheckServer: () => void;
}

/** 조각을 만들 때 필요한 호스트 능력 — 콜백 + 서버 상태에 연동되는 생성 버튼 팩토리 */
export interface PanelContext {
  callbacks: PanelCallbacks;
  /** 호스트가 만든 생성 버튼 (자기 목록에 등록해 setServerStatus로 일괄 갱신한다) */
  makeGenerateButton: (label: string, onClick: () => void) => HTMLButtonElement;
  /**
   * 지금 서버를 쓸 수 있는가 + 못 쓴다면 왜인지.
   * 조각들은 이걸 보고 "가사를 찾지 못했어요"와 "서버가 거부했어요"를 구분해 말한다.
   */
  server: ServerStatus;
  /** 디버그 모드 — 서버가 정상일 때도 요청 로그를 노출할지 결정한다 */
  debug: boolean;
  /** 최근 서버 요청 로그 — 접힌 섹션을 펼칠 때만 호출된다 */
  loadServerLog: () => Promise<ServerLogEntry[]>;
}

/** 서버 미가용 시 비활성 + 사유 툴팁이 붙는 '싱크 생성' 계열 버튼 */
export function createGenerateButton(
  label: string, server: ServerStatus, onClick: () => void,
): HTMLButtonElement {
  const btn = h('button', { className: 'ey-primary-btn ey-generate-btn', on: { click: onClick } },
    icon(ICONS.sparkle), label);
  applyServerGate(btn, server);
  return btn;
}

/** 서버가 필요한 컨트롤 하나를 상태에 맞춰 잠그거나 푼다 (사유는 툴팁으로) */
export function applyServerGate(
  btn: HTMLButtonElement, server: ServerStatus, enabledTitle = '',
): void {
  const usable = serverUsable(server);
  btn.disabled = !usable;
  btn.title = usable ? enabledTitle : serverBlockedTip(server);
  btn.classList.toggle('ey-server-blocked', !usable);
}

// ── 서버 상태 배너 + 요청 로그 ───────────────────────────────────

/**
 * 최근 서버 요청 로그 (기본 접힘).
 *
 * **언제 보이나**: 서버 상태가 정상이 아닐 때는 디버그 모드와 무관하게 항상,
 * 정상일 때는 디버그 모드에서만. 근거 — 뭔가 깨졌을 때 로그는 사용자가 원인을 짚거나
 * 그대로 옮겨 신고할 수 있는 유일한 증거라 숨기면 안 되고, 멀쩡할 때는 평상시 화면을
 * 어지럽히는 잡음일 뿐이다. 어느 경우든 **접힌 채로** 시작해 화면을 점유하지 않는다.
 */
export function buildServerLogSection(ctx: PanelContext): HTMLDetailsElement | null {
  if (!serverKnownBad(ctx.server) && !ctx.debug) return null;

  const list = h('div', { className: 'ey-log-list ey-state-sub', text: '펼치면 불러와요' });
  const details = h('details', { className: 'ey-log' },
    h('summary', { className: 'ey-log-summary', text: '최근 서버 요청 기록' }),
    list,
  );
  let loading = false;
  const refresh = () => {
    if (loading) return;
    loading = true;
    list.replaceChildren(h('div', { className: 'ey-state-sub', text: '불러오는 중…' }));
    void ctx.loadServerLog().then(entries => {
      loading = false;
      if (entries.length === 0) {
        list.replaceChildren(h('div', { className: 'ey-state-sub', text: '기록이 없어요 (확장을 다시 로드하면 초기화돼요)' }));
        return;
      }
      // 경로·본문의 키류는 기록 시점에 이미 마스킹됐다 — 여기서는 그대로 그린다
      list.replaceChildren(...entries.map(e =>
        h('div', { className: `ey-log-row${e.ok ? '' : ' bad'}`, text: formatLogEntry(e) })));
    });
  };
  details.addEventListener('toggle', () => {
    if (details.open) refresh();
  });
  return details;
}

/**
 * 서버 문제 배너 — 사유 한 줄 + 원인 코드 + 복구 동작 + 접이식 로그.
 *
 * 메인 패널과 PiP가 **같은 이 조각**을 쓴다. 다만 그리는 자리는 각자의 창 골격
 * (헤더 아래 / 푸터 위)이다 — 본문 상태 화면들이 각자 또 그리면 같은 배너가 두 번
 * 보인다. 그래서 아래 build*State들은 이 함수를 부르지 않고 호스트에 맡긴다.
 * 정상이거나 아직 확인 전이면 null이다 (첫 확인 전 빨간 배너는 없는 오류를 깜빡인다).
 */
export function buildServerStatusBar(ctx: PanelContext): HTMLDivElement | null {
  const status = ctx.server;
  if (!serverKnownBad(status)) return null;

  const bar = h('div', { className: `ey-server-bar ey-server-${status.kind}` });
  const head = h('div', { className: 'ey-server-bar-head' },
    h('span', { className: 'ey-server-bar-icon', text: status.kind === 'auth' ? '🔑' : '⚠️' }),
    h('span', { className: 'ey-server-bar-text', text: statusLine(status) }),
  );
  bar.append(head);
  // 서버가 준 힌트가 있으면 그대로 — 사용자가 서버 로그를 뒤지지 않아도 되게
  if (status.detail) bar.append(h('div', { className: 'ey-server-bar-detail', text: status.detail }));

  const actions = h('div', { className: 'ey-server-bar-actions' },
    h('button', {
      className: 'ey-secondary-btn',
      text: '다시 확인',
      attrs: { title: '서버에 다시 연결해 봅니다' },
      on: { click: () => ctx.callbacks.onRecheckServer() },
    }),
    h('button', {
      className: 'ey-secondary-btn',
      text: '설정 열기',
      attrs: { title: '서버 URL과 API 키를 확인할 수 있어요' },
      on: { click: () => ctx.callbacks.onOpenSettings() },
    }),
  );
  bar.append(actions);
  const log = buildServerLogSection(ctx);
  if (log) bar.append(log);
  return bar;
}

/**
 * 호스트 골격(메인 패널 헤더 아래 / PiP 푸터 위)에 넣을 서버 상태 표시 한 덩어리.
 *
 * - 서버 고장: 배너(사유·코드·복구 버튼·접이식 로그)
 * - 정상 + 디버그: 접이식 로그만
 * - 그 밖(정상, 또는 아직 확인 전): 아무것도 안 그린다
 */
export function buildServerStatusSlot(ctx: PanelContext): HTMLElement | null {
  return buildServerStatusBar(ctx) ?? buildServerLogSection(ctx);
}

/** 리스트 자리(.ey-result-list)에 한 줄 상태 메시지를 표시 */
export function setListStatus(listEl: HTMLElement | null, message: string): void {
  listEl?.replaceChildren(h('div', { className: 'ey-state-sub', text: message }));
}

// ── 검색 폼 ──────────────────────────────────────────────────────

export interface SearchFormRefs {
  el: HTMLDivElement;
  titleInput: HTMLInputElement;
  artistInput: HTMLInputElement;
  /** 현재 입력값으로 검색 실행 (제목이 비면 아무것도 하지 않는다) */
  submit: () => void;
}

/**
 * 제목·아티스트 입력 + 실행 버튼.
 * submitOnEnter는 호출부마다 다르다 — 빈 상태 폼은 예전부터 Enter를 처리하지 않았고
 * 검색 시트만 처리했다. 추출하면서 동작이 바뀌지 않도록 옵션으로 유지한다.
 */
export function buildSearchForm(
  initial: { title: string; artist: string },
  opts: { buttonLabel: string; submitOnEnter: boolean; onSubmit: (q: { title: string; artist: string }) => void },
): SearchFormRefs {
  const titleInput = h('input', { className: 'ey-input', attrs: { placeholder: '곡 제목' } });
  titleInput.value = initial.title;
  const artistInput = h('input', { className: 'ey-input', attrs: { placeholder: '아티스트 (선택)' } });
  artistInput.value = initial.artist;

  const submit = () => {
    const title = titleInput.value.trim();
    if (!title) return;
    opts.onSubmit({ title, artist: artistInput.value.trim() });
  };
  if (opts.submitOnEnter) {
    titleInput.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
    artistInput.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
  }

  const el = h('div', { className: 'ey-search-form' },
    titleInput,
    artistInput,
    h('button', { className: 'ey-primary-btn', text: opts.buttonLabel, on: { click: submit } }),
  );
  return { el, titleInput, artistInput, submit };
}

// ── 가사 검색 링크 (외부 사이트로 보내기만 한다) ────────────────────

/**
 * 가사를 못 찾았을 때 나무위키·구글·Genius 검색 결과로 보내는 링크.
 *
 * **스크래핑이 아니다** — 각 사이트의 검색 URL을 만들어 새 탭으로 여는 것이 전부이며,
 * 확장이 그 페이지에 요청을 보내거나 본문을 가져오지 않는다. 사용자가 자기 브라우저로
 * 방문해 가사를 확인하고, 원하면 붙여넣기 칸에 직접 옮겨 담는다.
 */
export function buildLyricsSearchLinks(song: SongInfo | null): HTMLDivElement {
  const wrap = h('div', { className: 'ey-search-links' });
  const query = [song?.title?.trim(), song?.artist?.trim()].filter(Boolean).join(' ');
  if (!query) return wrap; // 곡 정보가 없으면 검색어를 만들 수 없다 (CSS로 빈 wrap은 숨김)

  const enc = encodeURIComponent;
  const targets: { label: string; url: string }[] = [
    { label: '나무위키', url: `https://namu.wiki/Search?q=${enc(query)}` },
    { label: '구글', url: `https://www.google.com/search?q=${enc(`${query} 가사`)}` },
    { label: 'Genius', url: `https://genius.com/search?q=${enc(query)}` },
  ];
  wrap.append(
    h('div', { className: 'ey-state-sub', text: '다른 곳에서 가사 찾아보기' }),
    h('div', { className: 'ey-search-links-row' }, ...targets.map(t =>
      h('a', {
        className: 'ey-secondary-btn ey-search-link',
        text: t.label,
        attrs: { href: t.url, target: '_blank', rel: 'noopener noreferrer', title: `${t.label}에서 "${query}" 검색 (새 탭)` },
      }))),
  );
  return wrap;
}

// ── 가사 붙여넣기 섹션 ────────────────────────────────────────────

/** 가사 직접 붙여넣기 섹션 (토글 접힘) — 빈 상태·검색 시트 공용 */
export function buildPasteSection(ctx: PanelContext, startOpen = false): HTMLDivElement {
  const lyricsArea = h('textarea', {
    className: 'ey-textarea',
    attrs: {
      placeholder: '여기에 가사를 붙여넣으면 AI가 타이밍을 맞춰줘요\n'
        + '유의: 제목·섹션 표기 등 없이 줄바꿈만 있는 원문 언어 가사여야 합니다.',
      rows: '6',
    },
  });
  // 붙여넣기 칸의 한 줄 안내 자리 (빈 채로 생성을 누른 경우 등).
  // 예전에는 여기에 '이 영상 자막에서 가사 가져오기' 버튼과 트랙 목록이 붙어 있었지만,
  // 자막은 이제 싱크가 없을 때 content.ts가 알아서 폴백하므로 고르는 단계 자체가 없다.
  const statusEl = h('div', { className: 'ey-result-list' });
  // 출처는 선택 입력 — 어디서 옮겨온 가사인지 남겨두면 나중에 출처 표기를 되살릴 수 있다.
  // 비워도 생성은 그대로 진행된다 (강제하지 않는다).
  const attributionInput = h('input', {
    className: 'ey-input',
    attrs: {
      placeholder: '출처 (선택) — 예: 나무위키, Genius',
      title: '가사를 어디서 가져왔는지 적어두면 싱크에 함께 저장돼 출처 배지에 표시돼요. 비워도 됩니다.',
    },
  });
  const pasteSection = h('div', { className: 'ey-paste-section' },
    lyricsArea,
    h('div', {
      className: 'ey-state-sub',
      text: '유의: 제목·섹션 표기([Verse] 등)가 섞이면 타이밍이 어긋나요 — 원문 가사 줄만 넣어 주세요',
    }),
    statusEl,
    attributionInput,
    ctx.makeGenerateButton('붙여넣은 가사로 싱크 생성', () => {
      const text = lyricsArea.value.trim();
      if (!text) {
        // 빈 채로 눌렀을 때 무반응이면 버튼이 죽은 줄 안다 — 안내 후 입력칸으로 포커스
        setListStatus(statusEl, '가사를 먼저 붙여넣어 주세요');
        lyricsArea.focus();
        return;
      }
      ctx.callbacks.onGenerate(text, attributionInput.value.trim() || undefined);
    }),
  );
  pasteSection.style.display = startOpen ? '' : 'none';

  const pasteToggle = h('button', {
    className: 'ey-secondary-btn',
    text: startOpen ? '붙여넣기 닫기' : '가사 직접 붙여넣기',
    on: {
      click: () => {
        const hidden = pasteSection.style.display === 'none';
        pasteSection.style.display = hidden ? '' : 'none';
        pasteToggle.textContent = hidden ? '붙여넣기 닫기' : '가사 직접 붙여넣기';
      },
    },
  });
  return h('div', { className: 'ey-paste-wrap' }, pasteToggle, pasteSection);
}

// ── 상태 화면 ────────────────────────────────────────────────────

/**
 * 서버를 쓸 수 없을 때의 화면 — "가사를 찾지 못했어요" 대신 여기로 온다.
 *
 * 사용자가 겪은 문제가 정확히 이것이다: 서버가 401을 돌려줬는데 화면은 곡에 가사가
 * 없는 것처럼 굴었다. 그래서 여기서는 **서버 문제라고 먼저 말하고**, 서버가 필요한
 * 조작(싱크 생성·후보 검색·붙여넣기)은 아예 내놓지 않는다 — 눌러도 실패할 버튼을
 * 보여 주는 건 작동하는 척하는 것이다. 반대로 **서버 없이 되는 것**(다른 사이트에서
 * 가사 찾기, 자동 검색 재시도)은 그대로 남긴다.
 */
export function buildServerDownState(ctx: PanelContext, song: SongInfo | null): HTMLDivElement {
  const status = ctx.server;
  // 사유·원인 코드·복구 버튼·로그는 호스트가 그리는 배너(buildServerStatusBar)에 있다.
  // 여기서는 "이 화면이 왜 비었는지"만 말한다.
  const el = h('div', { className: 'ey-state' },
    h('div', { className: 'ey-state-emoji', text: status.kind === 'auth' ? '🔑' : '🔌' }),
    h('div', { className: 'ey-state-text', text: status.reason || '서버를 쓸 수 없어요' }),
  );
  if (status.code) el.append(h('div', { className: 'ey-state-sub', text: status.code }));
  el.append(
    // 서버가 죽어도 LRCLIB·위키 조회는 백그라운드가 직접 하므로, 여기까지 왔다는 건
    // 외부 소스에도 이 곡 가사가 없었다는 뜻이다 — 둘 다 사실대로 말한다
    h('div', {
      className: 'ey-state-sub',
      text: '가사 검색은 서버 없이도 되지만 이 곡은 외부 소스에서도 찾지 못했어요. '
        + '싱크 생성·재생성·번역은 서버가 있어야 해요.',
    }),
    buildLyricsSearchLinks(song),
    h('button', {
      className: 'ey-secondary-btn',
      text: '가사 다시 검색',
      attrs: { title: '서버를 고친 뒤 누르면 처음부터 다시 찾아봐요' },
      on: { click: () => ctx.callbacks.onRetrySearch() },
    }),
  );
  return el;
}

/** "가사를 찾지 못했어요" — 재검색 폼 + 외부 검색 링크 + 붙여넣기 */
export function buildEmptyState(ctx: PanelContext, song: SongInfo | null): HTMLDivElement {
  // 서버가 고장난 것이 확인됐다면 "가사를 찾지 못했어요"는 거짓말이 된다 — 서버 문제를
  // 먼저 말한다. 아직 확인 전(unknown)이면 단정하지 않고 평소 화면을 그대로 쓴다.
  if (serverKnownBad(ctx.server)) return buildServerDownState(ctx, song);

  const form = buildSearchForm(
    { title: song?.title ?? '', artist: song?.artist ?? '' },
    {
      buttonLabel: '다시 검색',
      submitOnEnter: false,
      onSubmit: q => ctx.callbacks.onRetrySearch(q),
    },
  );
  return h('div', { className: 'ey-state' },
    h('div', { className: 'ey-state-emoji', text: '🎵' }),
    h('div', { className: 'ey-state-text', text: '가사를 찾지 못했어요' }),
    form.el,
    h('button', {
      className: 'ey-secondary-btn',
      text: '상세 검색 (후보 선택·싱크 연결)',
      on: { click: () => ctx.callbacks.onOpenSearch() },
    }),
    buildLyricsSearchLinks(song),
    h('div', { className: 'ey-divider' }),
    buildPasteSection(ctx, true),
  );
}

/** 검색 중 스켈레톤 */
export function buildLoadingState(ctx: PanelContext, message: string): HTMLDivElement {
  const skeleton = h('div', { className: 'ey-skeleton' });
  for (let i = 0; i < 3; i++) skeleton.append(h('div', { className: 'ey-skeleton-bar' }));
  return h('div', { className: 'ey-state' },
    skeleton,
    h('div', { className: 'ey-state-text', text: message }),
    // 자동 검색을 기다릴 필요 없이 바로 수동 검색으로 전환
    h('button', {
      className: 'ey-secondary-btn',
      text: '기다리지 않고 수동 검색',
      on: { click: () => ctx.callbacks.onOpenSearch() },
    }),
  );
}

/**
 * 오류 상태 — 무엇이 실패했는지(message)와 왜인지(서버 상태 배너)를 같이 보여 준다.
 * detail은 호출부가 아는 추가 사유(예: 서버가 준 힌트)로, 배너와 별개로 한 줄 더 남긴다.
 */
export function buildErrorState(ctx: PanelContext, message: string, detail?: string): HTMLDivElement {
  const el = h('div', { className: 'ey-state' },
    h('div', { className: 'ey-state-emoji', text: '⚠️' }),
    h('div', { className: 'ey-state-text', text: message }),
  );
  if (detail) el.append(h('div', { className: 'ey-state-sub', text: detail }));
  el.append(h('button', {
    className: 'ey-primary-btn',
    text: '다시 시도',
    on: { click: () => ctx.callbacks.onRetrySearch() },
  }));
  return el;
}

export interface GeneratingStateRefs {
  el: HTMLDivElement;
  bar: HTMLDivElement;
  text: HTMLDivElement;
}

/** 싱크 생성 진행 상태 (진행 바 + 문구) */
export function buildGeneratingState(pct: number, text: string): GeneratingStateRefs {
  const bar = h('div', { className: 'ey-progress-bar' });
  bar.style.width = `${pct}%`;
  const textEl = h('div', { className: 'ey-state-text', text });
  const el = h('div', { className: 'ey-state' },
    h('div', { className: 'ey-state-emoji', text: '✨' }),
    textEl,
    h('div', { className: 'ey-progress' }, bar),
    h('div', { className: 'ey-state-sub', text: '계속 시청하셔도 돼요. 완료되면 자동으로 표시됩니다.' }),
  );
  return { el, bar, text: textEl };
}

/** 타임싱크 없는 일반 가사 목록 */
export function buildPlainLines(lines: LyricLine[]): { el: HTMLDivElement; lineEls: HTMLElement[] } {
  const list = h('div', { className: 'ey-lines ey-lines-plain' });
  const lineEls: HTMLElement[] = [];
  for (const line of lines) {
    const el = h('div', { className: 'ey-line ey-line-plain', text: line.text, attrs: { dir: 'auto' } });
    if (line.pronunciation) el.append(h('div', { className: 'ey-line-pron', text: line.pronunciation, attrs: { dir: 'auto' } }));
    if (line.translation) el.append(h('div', { className: 'ey-line-tr', text: line.translation, attrs: { dir: 'auto' } }));
    lineEls.push(el);
    list.append(el);
  }
  return { el: list, lineEls };
}

// ── 검색 시트 ────────────────────────────────────────────────────

export interface SearchSheetRefs {
  el: HTMLDivElement;
  /** 후보 결과가 그려지는 자리 */
  results: HTMLDivElement;
  /** 현재 입력값으로 후보 검색 실행 */
  runSearch: () => void;
}

/**
 * 상시 재검색 시트 — 검색 폼 + 후보 리스트 + 붙여넣기.
 * extras는 호스트별 추가 섹션(메인 패널의 '다른 영상 싱크 연결'·'싱크 초기화' 등)을
 * 붙여넣기 아래에 그대로 이어 붙인다.
 */
export function buildSearchSheet(
  ctx: PanelContext,
  state: { title: string; artist: string },
  opts: { onBack: () => void; extras?: (Node | null)[] },
): SearchSheetRefs {
  const results = h('div', { className: 'ey-result-list' });
  const form = buildSearchForm(state, {
    buttonLabel: '검색',
    submitOnEnter: true,
    onSubmit: q => {
      setListStatus(results, '검색 중…');
      ctx.callbacks.onCandidateSearch(q);
    },
  });
  const el = h('div', { className: 'ey-state ey-search-state' },
    h('button', {
      className: 'ey-secondary-btn ey-search-back',
      text: '← 보던 가사로 돌아가기',
      on: { click: () => opts.onBack() },
    }),
    h('div', { className: 'ey-state-text', text: '가사 검색 — 결과에서 직접 선택할 수 있어요' }),
    h('div', { className: 'ey-state-sub', text: '보카로 위키(발음·번역 포함) + LRCLIB(싱크 가사)를 함께 검색합니다' }),
  );
  // 후보 검색 자체는 서버가 없어도 된다 (LRCLIB은 확장이 직접 조회한다) — 그래서 잠그지
  // 않는다. 다만 위키 원제 매칭은 서버 인덱스를 쓰므로 결과가 줄어들 수 있음을 알린다.
  // (사유·복구 버튼은 호스트가 그리는 배너에 있으므로 여기서 반복하지 않는다)
  if (serverKnownBad(ctx.server)) {
    el.append(h('div', {
      className: 'ey-state-sub',
      text: 'LRCLIB 검색은 그대로 되지만, 보카로 위키 원제 매칭과 싱크 생성은 서버가 필요해요.',
    }));
  }
  el.append(
    form.el,
    results,
    h('div', { className: 'ey-divider' }),
    buildPasteSection(ctx),
  );
  for (const extra of opts.extras ?? []) {
    if (extra) el.append(extra);
  }
  return { el, results, runSearch: form.submit };
}

// ── 결과 리스트 렌더 ─────────────────────────────────────────────

/** SEARCH_CANDIDATES 응답을 후보 버튼 목록으로 그린다 */
export function renderCandidateList(
  listEl: HTMLElement, candidates: SearchCandidate[], onPick: (c: SearchCandidate) => void,
): void {
  if (candidates.length === 0) {
    setListStatus(listEl, '결과가 없어요 — 제목을 줄이거나 원제(일본어)로 시도해 보세요');
    return;
  }
  const fmt = (sec: number) => `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, '0')}`;
  listEl.replaceChildren(...candidates.map(c => {
    const isWiki = c.source === 'vocaro';
    const label = isWiki ? c.title : `${c.title}${c.artist ? ' — ' + c.artist : ''}`;
    const meta = isWiki
      ? '발음·번역'
      : `${c.synced ? '싱크' : '일반'}${c.duration > 0 ? ` · ${fmt(c.duration)}` : ''}`;
    const btn = h('button', {
      className: 'ey-result-item',
      on: { click: () => onPick(c) },
    },
      h('span', { className: `ey-result-src${isWiki ? ' vocaro' : ''}`, text: isWiki ? '보카로 위키' : 'LRCLIB' }),
      h('span', { className: 'ey-result-title', text: label }),
      h('span', { className: 'ey-result-meta', text: meta }),
    );
    btn.title = isWiki ? c.url : label;
    return btn;
  }));
}
