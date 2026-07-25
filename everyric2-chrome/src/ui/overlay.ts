import type { DebugInfo, LyricLine, LyricsSource, PanelGeometry, SearchCandidate, ServerLogEntry, ServerStatus, Settings, SongInfo, SyncListItem } from '../types';
import { serverUsable, statusLine, unknownStatus } from '../lib/server-status';
import { resolveTheme } from '../lib/theme';
import { h, icon, ICONS } from './dom';
import { appendKaraokeSpans, appendTimedSpans } from './karaoke';
import {
  applyServerGate,
  buildEmptyState,
  buildErrorState,
  buildGeneratingState,
  buildLoadingState,
  buildPlainLines,
  buildSearchSheet,
  buildServerStatusSlot,
  createGenerateButton,
  renderCandidateList,
  setListStatus,
  type PanelContext,
} from './panels';

export interface OverlayCallbacks {
  onSeek: (time: number) => void;
  /** attribution은 붙여넣기 경로에서 사용자가 적어 넣은 출처(선택) */
  onGenerate: (lyrics: string, attribution?: string) => void;
  onRetrySearch: (query?: { title: string; artist: string }) => void;
  onOffsetChange: (offsetSec: number) => void;
  onSettingsChange: (patch: Partial<Settings>) => void;
  /** 현재 everyric 싱크의 강제 재생성 (서버 캐시 무시) */
  onRegenerate: () => void;
  onPipToggle: () => void;
  onGeometryChange: (geometry: PanelGeometry) => void;
  /** 수동 검색: 후보 리스트 요청 — 결과는 showSearchResults로 되돌아온다 */
  onCandidateSearch: (query: { title: string; artist: string }) => void;
  /** 후보 리스트에서 사용자가 직접 선택 */
  onPickCandidate: (candidate: SearchCandidate) => void;
  /** 다른 영상의 싱크에 연결 (inst·커버) — rate는 원곡 대비 배속(nightcore≈1.25) */
  onLinkSync: (sourceVideoId: string, offsetSec: number, rate: number) => void;
  /** 진행 중인 전사 잡 취소 (진행 칩 클릭) */
  onCancelGenerate: () => void;
  /** 현재 영상의 싱크 링크 해제 */
  onUnlinkSync: () => void;
  /** 서버 저장 싱크 목록 요청 — 결과는 showSyncList로 되돌아온다 */
  onRequestSyncList: () => void;
  /** 이 영상의 서버 싱크 전부 삭제(초기화) — 잘못 붙여넣은 가사에서 새로 시작 */
  onResetSync: () => void;
  /** 검색 시트에서 원래 보던 가사 화면으로 복귀 (실수로 검색을 연 경우 탈출구) */
  onCloseSearch: () => void;
  /** 서버 상태 다시 확인 (서버 오류 배너의 '다시 확인') */
  onRecheckServer: () => void;
  /** 최근 서버 요청 로그 — 접이식 섹션을 펼칠 때만 호출된다 */
  loadServerLog: () => Promise<ServerLogEntry[]>;
}

type StateKind = 'loading' | 'synced' | 'plain' | 'empty' | 'generating' | 'error' | 'pip' | 'search';

/** confidence(CTC 확률 기하평균, 0~1)를 e표기 없이 10진수로 — 아주 작은 값도 첫 유효숫자까지 */
function fmtConf(v: number): string {
  if (!(v > 0)) return '0';
  if (v >= 0.001) return v.toFixed(3);
  const digits = Math.min(10, 1 - Math.floor(Math.log10(v)));
  return v.toFixed(digits);
}

/** 유튜브 URL 또는 순수 11자리 ID에서 videoId 추출 */
function parseVideoId(input: string): string | null {
  if (/^[\w-]{11}$/.test(input)) return input;
  const m = input.match(/(?:v=|youtu\.be\/|\/shorts\/|\/embed\/)([\w-]{11})/);
  return m ? m[1] : null;
}

const DEFAULT_WIDTH = 340;
const DEFAULT_HEIGHT = 480;
const EDGE_MARGIN = 8;
const USER_SCROLL_HOLD_MS = 4000;

export class LyricsOverlay {
  private host: HTMLDivElement;
  private panel: HTMLDivElement;
  private header: HTMLDivElement;
  private songTitleEl: HTMLDivElement;
  private songArtistEl: HTMLDivElement;
  private body: HTMLDivElement;
  private footer: HTMLDivElement;
  private debugEl: HTMLDivElement;
  private banner: HTMLDivElement;
  private resumeChip: HTMLButtonElement;
  private genChip: HTMLDivElement;
  private genList: HTMLDivElement;
  private genListOpen = false;
  private genListItems: { title: string; state: string; isCurrent: boolean }[] = [];
  /** 알림 칩 — 커버 자동 연결 진행/결과, 붙여넣기 표기 필터 결과 등 한 줄 알림 */
  private noticeChip: HTMLDivElement;
  private noticeTimer = 0;
  private warnBar: HTMLDivElement;
  /** 서버 오류 배너 — body 밖에 있어 resetBody()로 지워지지 않는다.
   *  덕분에 어떤 화면(가사·검색·생성 중·오류)에서도 사유 한 줄이 반드시 보인다. */
  private serverBar: HTMLDivElement;
  private pipBtn: HTMLButtonElement;
  private regenBtn: HTMLButtonElement;
  private collapseBtn: HTMLButtonElement;
  private settingsSheet: HTMLDivElement | null = null;
  private settingsDot: HTMLSpanElement | null = null;
  private sourceBadge: HTMLSpanElement;
  private offsetLabel: HTMLSpanElement;
  private progressBar: HTMLDivElement | null = null;
  private progressText: HTMLDivElement | null = null;

  private settings: Settings;
  private readonly callbacks: OverlayCallbacks;

  private stateKind: StateKind = 'loading';
  private lines: LyricLine[] = [];
  private lineEls: HTMLElement[] = [];
  private trStatusEl: HTMLSpanElement;
  private activeWordEls: { start: number; el: HTMLElement }[] = [];
  private currentIndex = -1;
  private userScrollUntil = 0;
  private offsetSec: number;
  private visible = true;
  private fullscreenHidden = false;
  private serverStatus: ServerStatus = unknownStatus();
  private generateButtons: HTMLButtonElement[] = [];
  private plainTextForGenerate = '';
  private pipEnabled = false;
  private sourceUrl: string | null = null;
  private attributionName: string | null = null;
  private lastSong: SongInfo | null = null;
  private searchResultsEl: HTMLDivElement | null = null;
  private linkListEl: HTMLDivElement | null = null;
  private linkSrcInput: HTMLInputElement | null = null;
  private linkFilterInput: HTMLInputElement | null = null;
  private syncListItems: SyncListItem[] | null = null;
  /** 현재 표시 중인 싱크의 링크 상태 (없으면 null) — content가 setLinked로 밀어넣는다.
   *  verified는 반주 대조로 검증된 자동 링크 여부 (undefined = 구버전 서버로 알 수 없음) */
  private linkedInfo: {
    sourceVideoId: string; offsetSec: number; rate?: number; verified?: boolean;
  } | null = null;

  private geometry: PanelGeometry;
  private applyingGeometry = false;
  private saveGeomTimer = 0;
  private resizeObserver: ResizeObserver;

  constructor(cssText: string, settings: Settings, callbacks: OverlayCallbacks, geometry: PanelGeometry | null) {
    this.settings = settings;
    this.callbacks = callbacks;
    this.offsetSec = settings.offsetSec;

    this.host = h('div', { attrs: { id: 'everyric-root' } });
    this.host.style.cssText = 'all:initial;position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;';
    const shadow = this.host.attachShadow({ mode: 'open' });

    const style = document.createElement('style');
    style.textContent = cssText;
    shadow.append(style);

    this.songTitleEl = h('div', { className: 'ey-song-title', text: '노래 인식 중…' });
    this.songArtistEl = h('div', { className: 'ey-song-artist' });

    this.pipBtn = this.headerButton(ICONS.pip, 'PiP 창으로 보기', () => this.callbacks.onPipToggle());
    this.pipBtn.style.display = 'none';
    this.regenBtn = this.headerButton(ICONS.refresh, '싱크 다시 생성 (서버 캐시 무시)', () => {
      if (window.confirm('서버 싱크를 다시 생성할까요?\n1분 정도 걸리고, 완료될 때까지 기존 가사는 계속 표시됩니다.')) {
        this.callbacks.onRegenerate();
      }
    });
    this.regenBtn.style.display = 'none';
    const searchBtn = this.headerButton(ICONS.search, '가사 다시 검색 (다른 결과 선택)', () => this.openSearch());
    const gearBtn = this.headerButton(ICONS.gear, '설정', () => this.toggleSettings());
    this.collapseBtn = this.headerButton(ICONS.collapse, '접기', () => this.setCollapsed(!this.geometry.collapsed));
    const closeBtn = this.headerButton(ICONS.close, '닫기 (툴바 아이콘으로 다시 열기)', () => this.setVisible(false));

    this.header = h('div', { className: 'ey-header' },
      h('div', { className: 'ey-header-left' },
        icon(ICONS.note),
        h('div', { className: 'ey-song' }, this.songTitleEl, this.songArtistEl),
      ),
      h('div', { className: 'ey-actions' }, this.pipBtn, this.regenBtn, searchBtn, gearBtn, this.collapseBtn, closeBtn),
    );

    this.banner = h('div', { className: 'ey-banner' });
    this.banner.style.display = 'none';

    // 전사 진행 칩 — 패널을 점유하지 않고 헤더 밑에 작게 진행률만 보여준다
    this.genChip = h('div', { className: 'ey-gen-chip' }, icon(ICONS.sparkle), '');
    this.genChip.style.display = 'none';

    // 칩 클릭 시 펼쳐지는 내 생성 대기열 목록 — 이 브라우저에서 시킨 잡만 저장돼
    // 있으므로(activeJobs) 다른 사용자의 큐는 구조적으로 보이지 않는다
    this.genList = h('div', { className: 'ey-gen-list' });
    this.genList.style.display = 'none';
    this.genChip.style.cursor = 'pointer';
    this.genChip.title = '클릭하면 내 생성 대기열 목록을 펼쳐요';
    this.genChip.addEventListener('click', () => {
      this.genListOpen = !this.genListOpen;
      this.renderGenList();
    });

    // 알림 칩 — 전사 진행 칩과 같은 모양·같은 자리 규약을 쓰되 **별개 엘리먼트**다.
    // (전사 진행과 자동 연결 확인은 동시에 일어날 수 있어 한 칩을 공유하면 서로를 지운다)
    this.noticeChip = h('div', { className: 'ey-gen-chip ey-notice-chip' });
    this.noticeChip.style.display = 'none';

    // 낮은 정렬 신뢰도 경고 바 — X로 닫을 수 있고 설정에서 아예 끌 수 있다
    this.warnBar = h('div', { className: 'ey-warn-bar' });
    this.warnBar.style.display = 'none';

    // 서버 오류 배너 — 상태가 정상/미확인이면 비어 있고, 아니면 사유+복구 동작이 들어간다
    this.serverBar = h('div', { className: 'ey-server-bar-slot' });
    this.serverBar.style.display = 'none';

    this.body = h('div', {
      className: 'ey-body',
      on: {
        wheel: () => this.markUserScroll(),
        touchmove: () => this.markUserScroll(),
        pointerdown: () => this.markUserScroll(),
      },
    });

    this.resumeChip = h('button', {
      className: 'ey-resume-chip',
      on: { click: () => this.resumeAutoScroll() },
    }, icon(ICONS.down), '현재 가사로');
    this.resumeChip.style.display = 'none';

    this.sourceBadge = h('span', {
      className: 'ey-source',
      on: {
        click: () => {
          if (this.sourceUrl) window.open(this.sourceUrl, '_blank', 'noopener');
        },
      },
    });
    this.trStatusEl = h('span', { className: 'ey-tr-status' });
    this.offsetLabel = h('span', { className: 'ey-offset-value', text: '0.0s' });
    this.footer = h('div', { className: 'ey-footer' },
      this.sourceBadge,
      this.trStatusEl,
      h('div', { className: 'ey-offset' },
        h('span', { className: 'ey-offset-caption', text: '싱크' }),
        this.footerButton('−0.1', '가사를 0.1초 당기기', () => this.changeOffset(-0.1)),
        this.offsetLabel,
        this.footerButton('+0.1', '가사를 0.1초 늦추기', () => this.changeOffset(0.1)),
        this.footerButton('리셋', '오프셋 초기화', () => this.changeOffset(null)),
      ),
    );
    this.footer.style.display = 'none';

    this.debugEl = h('div', { className: 'ey-debug', text: 'debug: 대기 중…' });
    this.debugEl.style.display = 'none';

    this.panel = h('div', { className: 'ey-panel' },
      this.header, this.serverBar, this.banner, this.genChip, this.genList, this.noticeChip,
      this.warnBar, this.body, this.resumeChip, this.footer, this.debugEl,
    );
    // 패널 안 타이핑(검색창·가사 붙여넣기)이 유튜브 전역 단축키(스페이스=재생/정지,
    // 방향키=시킹 등)로 새지 않도록 키 이벤트를 패널에서 끊는다
    for (const type of ['keydown', 'keyup', 'keypress'] as const) {
      this.panel.addEventListener(type, e => e.stopPropagation());
    }
    shadow.append(this.panel);

    this.geometry = geometry ?? this.defaultGeometry();
    this.applyGeometry();
    this.applySettings(settings);
    this.updateOffsetLabel();

    this.setupDrag();
    this.resizeObserver = new ResizeObserver(() => this.handlePanelResize());
    this.resizeObserver.observe(this.panel);
    window.addEventListener('resize', this.handleWindowResize);
    document.addEventListener('fullscreenchange', this.handleFullscreenChange);

    document.documentElement.append(this.host);
  }

  /** 현재 오버레이는 페이지 수명 싱글턴이라 호출처가 없다 — 향후 하드 teardown 경로용 */
  destroy(): void {
    this.resizeObserver.disconnect();
    window.removeEventListener('resize', this.handleWindowResize);
    document.removeEventListener('fullscreenchange', this.handleFullscreenChange);
    clearTimeout(this.saveGeomTimer);
    this.host.remove();
  }

  // ── 상태 렌더링 ────────────────────────────────────────────────

  /** 패널 조각(panels.ts)에 넘기는 호스트 컨텍스트 — 콜백 + 서버 상태 연동 생성 버튼 */
  private panelContext(): PanelContext {
    return {
      callbacks: {
        onGenerate: (lyrics, attribution) => this.callbacks.onGenerate(lyrics, attribution),
        onRetrySearch: query => this.callbacks.onRetrySearch(query),
        onCandidateSearch: query => this.callbacks.onCandidateSearch(query),
        onPickCandidate: candidate => this.callbacks.onPickCandidate(candidate),
        onOpenSearch: () => this.openSearch(),
        onOpenSettings: () => this.openSettings(),
        onRecheckServer: () => this.callbacks.onRecheckServer(),
      },
      makeGenerateButton: (label, onClick) => this.makeGenerateButton(label, onClick),
      server: this.serverStatus,
      debug: this.settings.debugInfo,
      loadServerLog: () => this.callbacks.loadServerLog(),
    };
  }

  showLoading(message = '가사 검색 중…'): void {
    this.stateKind = 'loading';
    this.resetBody();
    this.body.append(buildLoadingState(this.panelContext(), message));
  }

  showSyncedLyrics(lines: LyricLine[], source: LyricsSource, plainText?: string): void {
    this.stateKind = 'synced';
    this.resetBody();
    this.lines = lines;
    this.lineEls = [];
    this.currentIndex = -1;

    // LRCLIB 등 외부 싱크 가사도 서버 전사를 만들면 음정 노트·발음 정렬·가라오케를 쓸 수 있다
    if (source !== 'everyric') {
      const text = plainText ?? lines.map(l => l.text).join('\n');
      this.showBanner('AI 전사로 가라오케(음정·발음)를 만들 수 있어요',
        this.makeGenerateButton('AI 전사 생성', () => this.callbacks.onGenerate(text)));
    }

    const list = h('div', { className: 'ey-lines' });
    lines.forEach((line, index) => {
      const el = h('div', {
        className: 'ey-line',
        title: '클릭해서 이 부분으로 이동',
        // dir=auto — RTL(아랍어·히브리어) 가사가 문장 방향대로 정렬되게
        attrs: { dir: 'auto' },
        on: {
          click: () => {
            if (line.time !== null) this.callbacks.onSeek(line.time);
          },
        },
      });
      // words가 없어도 호출 — appendKaraokeSpans가 음절 타이밍/라인 구간 비례
      // 배분으로 폴백해, 라인이 한 번에 통째로 켜지는 표시를 피한다
      appendKaraokeSpans(el, line, word => {
        // 신뢰도 등급 클래스 — .ey-show-conf(디버그 모드)에서만 색이 입혀진다.
        // 값은 CTC 프레임 로그확률의 기하평균(0~1) — 절대값이 작아 로그 스케일로 버킷:
        // <1e-4(로그 -9 이하)=낮음, <2e-2(로그 -4 이하)=중간
        const conf = word.confidence;
        // 버킷 색은 레인(pip.ts confBucketColor)과 동일: 빨강<1e-4, 노랑<2e-2, 초록=양호
        const confClass = conf == null ? '' : conf < 1e-4 ? ' ey-conf-low' : conf < 2e-2 ? ' ey-conf-mid' : ' ey-conf-ok';
        return h('span', { className: `ey-word${confClass}`, text: word.word, attrs: { 'data-start': String(word.start) } });
      });
      if (line.pronunciation) {
        // 음절 타이밍(pronSegments)이 있으면 단어처럼 부른 만큼 색이 차오르게 스팬으로
        // (사이 텍스트는 appendTimedSpans가 인접 span에 끼워 넣어 흰 글자 없이 칠해진다)
        const segs = line.pronSegments;
        const pronEl = h('div', { className: 'ey-line-pron', attrs: { dir: 'auto' } });
        const mapped = segs && segs.length > 0
          ? appendTimedSpans(pronEl, line.pronunciation, segs, s => s.text, seg =>
              h('span', {
                className: 'ey-pron-syl',
                text: seg.text,
                attrs: { 'data-start': String(seg.start) },
              }))
          : 0;
        if (mapped === 0) pronEl.replaceChildren(line.pronunciation);
        el.append(pronEl);
      }
      if (line.translation) el.append(h('div', { className: 'ey-line-tr', text: line.translation, attrs: { dir: 'auto' } }));
      el.dataset.index = String(index);
      this.lineEls.push(el);
      list.append(el);
    });
    this.body.append(list);

    this.setSourceBadge(source, true);
    this.footer.classList.remove('no-offset');
    this.footer.style.display = '';
    this.pipBtn.style.display = this.pipEnabled ? '' : 'none';
    // 재생성은 서버(everyric) 싱크에서만 의미가 있다
    this.regenBtn.style.display = source === 'everyric' ? '' : 'none';
  }

  showPlainLyrics(lines: LyricLine[], source: LyricsSource, plainText: string): void {
    this.stateKind = 'plain';
    this.resetBody();
    this.plainTextForGenerate = plainText;

    const generateBtn = this.makeGenerateButton('싱크 생성', () => this.callbacks.onGenerate(this.plainTextForGenerate));
    this.showBanner('타임싱크가 없는 가사예요', generateBtn);

    this.lines = lines;
    const plain = buildPlainLines(lines);
    this.lineEls.push(...plain.lineEls);
    this.body.append(plain.el);

    this.setSourceBadge(source, false);
    this.footer.classList.add('no-offset');
    this.footer.style.display = '';
  }

  showEmpty(song: SongInfo | null): void {
    this.stateKind = 'empty';
    this.resetBody();
    this.body.append(buildEmptyState(this.panelContext(), song));
  }

  /** 상시 재검색: 현재 곡 정보를 초기값으로 검색 폼 + 소스별 후보 리스트를 연다 */
  openSearch(): void {
    this.stateKind = 'search';
    this.resetBody();

    const sheet = buildSearchSheet(
      this.panelContext(),
      { title: this.lastSong?.title ?? '', artist: this.lastSong?.artist ?? '' },
      {
        onBack: () => this.callbacks.onCloseSearch(),
        // 메인 패널에만 있는 고급 섹션 — 다른 영상 싱크 연결과 서버 저장 삭제는
        // 실수 여파가 커서 PiP의 축약 검색 시트에는 넣지 않는다
        extras: [
          h('div', { className: 'ey-divider' }),
          this.buildLinkSection(),
          h('div', { className: 'ey-divider' }),
          h('button', {
            className: 'ey-secondary-btn',
            text: '자동 검색으로 되돌리기',
            on: { click: () => this.callbacks.onRetrySearch() },
          }),
          h('button', {
            className: 'ey-secondary-btn',
            text: '이 영상 싱크 초기화 (서버 저장 삭제)',
            attrs: { title: '잘못 붙여넣은 가사로 만든 싱크를 완전히 지우고 처음부터 다시 시작합니다' },
            on: {
              click: () => {
                if (window.confirm('이 영상의 서버 싱크(정렬·발음·번역 저장본)를 모두 삭제할까요?\n삭제 후 자동 검색이 다시 실행되고, 가사를 새로 붙여넣을 수 있어요.')) {
                  this.callbacks.onResetSync();
                }
              },
            },
          }),
        ],
      },
    );
    this.searchResultsEl = sheet.results;
    this.body.append(sheet.el);
    sheet.runSearch();
  }

  /** 다른 영상 싱크 연결 섹션 (inst·커버 영상용) — 검색 시트 하단 */
  private buildLinkSection(): HTMLDivElement {
    const srcInput = h('input', {
      className: 'ey-input',
      attrs: { placeholder: '원본 영상 URL 또는 ID (전사가 이미 있는 영상)' },
    });
    this.linkSrcInput = srcInput;
    const offsetInput = h('input', {
      className: 'ey-input ey-input-narrow',
      attrs: { type: 'number', step: '0.1', placeholder: '오프셋(초)', title: '이 영상이 원본보다 늦게 시작하면 +, 빠르면 -' },
    });
    offsetInput.value = this.linkedInfo ? String(this.linkedInfo.offsetSec) : '0';
    // 배속이 다른 커버(nightcore 등)는 고정 오프셋만으론 뒤로 갈수록 밀린다 — 서버가
    // t/배속+오프셋으로 시간축을 사상한다
    const rateInput = h('input', {
      className: 'ey-input ey-input-narrow',
      attrs: {
        type: 'number', step: '0.01', min: '0.5', max: '2', placeholder: '배속',
        title: '원곡 대비 재생 배속 — nightcore≈1.25, 같은 속도면 1',
      },
    });
    rateInput.value = this.linkedInfo?.rate ? String(this.linkedInfo.rate) : '1';
    this.linkListEl = h('div', { className: 'ey-result-list' });
    const filterInput = h('input', {
      className: 'ey-input',
      attrs: { placeholder: '저장 싱크 검색 — 가사 첫 줄·영상 ID·출처' },
      on: { input: () => this.renderSyncList() },
    });
    filterInput.style.display = 'none'; // 목록을 불러온 뒤에만 노출
    this.linkFilterInput = filterInput;

    const doLink = () => {
      const src = parseVideoId(srcInput.value.trim());
      if (!src) {
        this.setLinkStatus('영상 URL 또는 11자리 ID를 입력해 주세요');
        return;
      }
      const offset = Number(offsetInput.value) || 0;
      const rate = Math.min(2, Math.max(0.5, Number(rateInput.value) || 1));
      this.setLinkStatus('연결 중…');
      this.callbacks.onLinkSync(src, offset, rate);
    };

    const section = h('div', { className: 'ey-link-section' },
      h('div', { className: 'ey-state-text', text: '다른 영상의 싱크 연결 (inst·커버용)' }),
    );
    if (this.linkedInfo) {
      const rateBadge = this.linkedInfo.rate && this.linkedInfo.rate !== 1
        ? ` ×${this.linkedInfo.rate}` : '';
      // 검증된 자동 연결과 검증 없는 수동 연결을 구분해 말한다 — 코퍼스에 검증 없는
      // 링크가 섞여 있어서, 가사가 어긋날 때 이 표시가 원인 판단의 첫 단서가 된다
      const verifyBadge = this.linkedInfo.verified === true ? ' · 검증됨'
        : this.linkedInfo.verified === false ? ' · 검증 없음' : '';
      section.append(h('div', { className: 'ey-link-current' },
        h('span', {
          text: `현재 ${this.linkedInfo.sourceVideoId}에 연결됨 (${this.linkedInfo.offsetSec >= 0 ? '+' : ''}${this.linkedInfo.offsetSec}s${rateBadge})${verifyBadge}`,
          attrs: { title: this.describeLink(this.linkedInfo) },
        }),
        h('button', {
          className: 'ey-secondary-btn',
          text: '링크 해제',
          on: { click: () => this.callbacks.onUnlinkSync() },
        }),
      ));
    }
    section.append(
      h('div', { className: 'ey-search-form' },
        srcInput,
        offsetInput,
        rateInput,
        h('button', { className: 'ey-primary-btn', text: '연결', on: { click: doLink } }),
      ),
      h('button', {
        className: 'ey-secondary-btn',
        text: '서버에 저장된 싱크 목록에서 고르기',
        on: {
          click: () => {
            this.setLinkStatus('목록 불러오는 중…');
            this.callbacks.onRequestSyncList();
          },
        },
      }),
      filterInput,
      this.linkListEl,
    );
    return section;
  }

  /** SYNC_LIST 응답 반영 — 목록을 캐시하고 검색 필터와 함께 렌더 */
  showSyncList(items: SyncListItem[]): void {
    if (this.stateKind !== 'search' || !this.linkListEl) return;
    this.syncListItems = items;
    if (this.linkFilterInput) {
      this.linkFilterInput.style.display = items.length === 0 ? 'none' : '';
      this.linkFilterInput.value = '';
    }
    if (items.length === 0) {
      this.setLinkStatus('서버에 저장된 싱크가 없어요');
      return;
    }
    this.renderSyncList();
  }

  /** 저장 싱크 목록 렌더 — 필터(가사 첫 줄·영상 ID·출처·정렬문) 적용 */
  private renderSyncList(): void {
    if (!this.linkListEl || !this.syncListItems) return;
    const q = (this.linkFilterInput?.value ?? '').trim().toLowerCase();
    const items = q
      ? this.syncListItems.filter(it =>
        it.video_id.toLowerCase().includes(q)
        || (it.first_line ?? '').toLowerCase().includes(q)
        || (it.attribution_name ?? '').toLowerCase().includes(q)
        || (it.alignment_text ?? '').toLowerCase().includes(q))
      : this.syncListItems;
    if (items.length === 0) {
      this.linkListEl.replaceChildren(
        h('div', { className: 'ey-state-sub', text: '검색과 일치하는 싱크가 없어요' }));
      return;
    }
    const hint = h('div', {
      className: 'ey-state-sub',
      text: '항목을 클릭하면 원본 칸에 채워져요 — 오프셋 확인 후 \'연결\'을 누르세요',
    });
    this.linkListEl.replaceChildren(hint, ...items.map(it => {
      const btn = h('button', { className: 'ey-result-item' },
        h('span', { className: 'ey-result-src', text: it.video_id }),
        h('span', { className: 'ey-result-title', text: it.first_line || '(첫 줄 없음)' }),
        h('span', { className: 'ey-result-meta', text: `${it.line_count}줄${it.attribution_name ? ' · ' + it.attribution_name : ''}` }),
      );
      btn.addEventListener('click', () => {
        if (this.linkSrcInput) this.linkSrcInput.value = it.video_id;
        this.linkListEl?.querySelectorAll('.ey-selected').forEach(el => el.classList.remove('ey-selected'));
        btn.classList.add('ey-selected');
      });
      return btn;
    }));
  }

  /** 링크 섹션 상태 메시지 (검색 상태가 아니면 무시) */
  setLinkStatus(message: string): void {
    setListStatus(this.linkListEl, message);
  }

  /** 현재 싱크의 링크 상태 — 검색 시트의 해제 UI와 출처 배지에 반영 */
  setLinked(
    info: { sourceVideoId: string; offsetSec: number; rate?: number; verified?: boolean } | null,
  ): void {
    this.linkedInfo = info;
  }

  /** SEARCH_CANDIDATES 응답 반영 — 검색 상태가 아니면 무시 (stale 응답 방지) */
  showSearchResults(candidates: SearchCandidate[]): void {
    if (this.stateKind !== 'search' || !this.searchResultsEl) return;
    renderCandidateList(this.searchResultsEl, candidates, c => this.callbacks.onPickCandidate(c));
  }

  showGenerating(progress: number, label?: string): void {
    const pct = Math.max(0, Math.min(100, Math.round(progress)));
    const text = label ?? `싱크 생성 중… ${pct}%`;
    if (this.stateKind === 'generating' && this.progressBar && this.progressText) {
      this.progressBar.style.width = `${pct}%`;
      this.progressText.textContent = text;
      return;
    }
    this.stateKind = 'generating';
    this.resetBody();
    const refs = buildGeneratingState(pct, text);
    this.progressBar = refs.bar;
    this.progressText = refs.text;
    this.body.append(refs.el);
  }

  /** detail은 서버가 준 힌트 등 추가 사유 — 있으면 문구 아래 한 줄로 함께 보여 준다 */
  showError(message: string, detail?: string): void {
    this.stateKind = 'error';
    this.resetBody();
    this.body.append(buildErrorState(this.panelContext(), message, detail));
  }

  showPipPlaceholder(): void {
    this.stateKind = 'pip';
    this.resetBody();
    this.body.append(
      h('div', { className: 'ey-state' },
        h('div', { className: 'ey-state-emoji', text: '🪟' }),
        h('div', { className: 'ey-state-text', text: 'PiP 창에서 가사를 표시하고 있어요' }),
        h('button', { className: 'ey-primary-btn', text: '패널로 되돌리기', on: { click: () => this.callbacks.onPipToggle() } }),
      ),
    );
  }

  // ── 싱크 업데이트 ──────────────────────────────────────────────

  highlightLine(index: number): void {
    if (this.stateKind !== 'synced') return;
    const prevIndex = this.currentIndex;
    this.currentIndex = index;
    this.activeWordEls = [];
    this.lineEls.forEach((el, i) => {
      el.classList.toggle('active', i === index);
      el.classList.toggle('past', index >= 0 && i < index);
    });
    // 되감기: sung은 활성 라인에만 토글되므로 앞으로 되돌아가면 미래가 된 줄들에
    // 이미 부른 표시가 남는다. 활성 라인보다 뒤쪽 줄의 sung을 걷어낸다
    // (활성 라인 자신은 곧 updateTime이 재계산한다).
    if (index < prevIndex) {
      for (let i = Math.max(index, -1) + 1; i < this.lineEls.length; i++) {
        for (const el of this.lineEls[i].querySelectorAll('.ey-word.sung, .ey-pron-syl.sung')) {
          el.classList.remove('sung');
        }
      }
    }
    const active = index >= 0 ? this.lineEls[index] : undefined;
    if (active) {
      // 발음 음절(.ey-pron-syl)도 단어와 같은 sung 토글 메커니즘에 합류
      for (const wordEl of active.querySelectorAll<HTMLElement>('.ey-word, .ey-pron-syl')) {
        this.activeWordEls.push({ start: Number(wordEl.dataset.start), el: wordEl });
      }
      if (Date.now() >= this.userScrollUntil) {
        this.scrollToCurrent();
      } else {
        this.resumeChip.style.display = '';
      }
    }
  }

  updateTime(time: number): void {
    for (const { start, el } of this.activeWordEls) {
      el.classList.toggle('sung', start <= time);
    }
  }

  // ── 외부 상태 주입 ─────────────────────────────────────────────

  setSong(song: SongInfo | null): void {
    this.lastSong = song;
    if (song) {
      this.songTitleEl.textContent = song.title;
      this.songTitleEl.title = song.title;
      this.songArtistEl.textContent = song.artist ?? '';
    } else {
      this.songTitleEl.textContent = '노래 인식 중…';
      this.songTitleEl.title = '';
      this.songArtistEl.textContent = '';
    }
  }

  setVisible(visible: boolean): void {
    this.visible = visible;
    this.updateHostVisibility();
  }

  isVisible(): boolean {
    return this.visible;
  }

  /**
   * 서버 상태 주입 — 사유까지 함께 받는다.
   *
   * 서버가 필요한 컨트롤(생성·재생성)을 잠그고 사유를 툴팁으로 붙이며, 배너를 갱신한다.
   * 지금 화면이 "가사를 찾지 못했어요"라면 그것도 서버 문제 화면으로 바꿔 준다 —
   * 상태 확인이 검색보다 늦게 끝나 잘못된 문구가 먼저 떠 있을 수 있기 때문이다.
   */
  setServerStatus(status: ServerStatus): void {
    const prevKind = this.serverStatus.kind;
    this.serverStatus = status;
    this.generateButtons = this.generateButtons.filter(btn => btn.isConnected);
    for (const btn of this.generateButtons) applyServerGate(btn, status);
    this.applyRegenGate();
    this.renderServerBar();

    if (this.settingsDot) {
      this.settingsDot.classList.toggle('ok', status.kind === 'ok');
      this.settingsDot.classList.toggle('auth', status.kind === 'auth');
      this.settingsDot.title = `서버 연결 상태 — ${statusLine(status)}`;
    }
    if (prevKind !== status.kind && this.stateKind === 'empty') {
      // 설정 시트에서 키를 고치던 중일 수 있다 — 화면은 다시 그리되 시트는 되살린다
      // (resetBody가 시트를 닫는다). 시트는 저장된 설정으로 새로 만들어진다.
      const settingsWasOpen = this.settingsSheet !== null;
      this.showEmpty(this.lastSong);
      if (settingsWasOpen) this.openSettings();
    }
  }

  /** 서버가 필요한 헤더 버튼(재생성) 잠금 — 표시 여부는 기존 로직 그대로 */
  private applyRegenGate(): void {
    applyServerGate(this.regenBtn, this.serverStatus, '싱크 다시 생성 (서버 캐시 무시)');
  }

  private renderServerBar(): void {
    const bar = buildServerStatusSlot(this.panelContext());
    if (!bar) {
      this.serverBar.replaceChildren();
      this.serverBar.style.display = 'none';
      return;
    }
    this.serverBar.replaceChildren(bar);
    this.serverBar.style.display = '';
  }

  setPipEnabled(enabled: boolean): void {
    this.pipEnabled = enabled;
    this.pipBtn.style.display = enabled && this.stateKind === 'synced' ? '' : 'none';
  }

  setPipActive(active: boolean): void {
    this.pipBtn.classList.toggle('active', active);
  }

  isShowingPipPlaceholder(): boolean {
    return this.stateKind === 'pip';
  }

  /** lines[].translation을 다시 읽어 각 라인 아래 번역을 갱신/제거한다 */
  refreshTranslations(): void {
    this.lineEls.forEach((el, i) => {
      el.querySelector('.ey-line-tr')?.remove();
      const line = this.lines[i];
      // 번역 API가 발음(한글 독음)을 늦게 채워주는 경우 — 렌더 후 붙은 발음도 표시
      if (line?.pronunciation && !el.querySelector('.ey-line-pron')) {
        el.append(h('div', { className: 'ey-line-pron', text: line.pronunciation, attrs: { dir: 'auto' } }));
      }
      if (line?.translation) el.append(h('div', { className: 'ey-line-tr', text: line.translation, attrs: { dir: 'auto' } }));
    });
  }

  setTranslationStatus(text: string | null): void {
    this.trStatusEl.textContent = text ?? '';
  }

  /** 낮은 정렬 신뢰도 경고 바 — score가 null이면 숨김. X로 닫을 수 있다. */
  setQualityWarning(score: number | null): void {
    if (score === null) {
      this.warnBar.style.display = 'none';
      return;
    }
    this.warnBar.replaceChildren(
      h('span', {
        className: 'ey-warn-text',
        text: `⚠️ 전사가 부정확할 수 있어요 (정렬 신뢰도 ${fmtConf(score)})`,
        attrs: { title: '전사·발음 정렬의 평균 신뢰도가 낮아요. 가사 원문이 정확한지 확인하거나 재생성을 시도해 보세요.' },
      }),
      h('button', {
        className: 'ey-warn-close',
        text: '×',
        title: '이 경고 닫기 (설정에서 끌 수도 있어요)',
        on: { click: () => { this.warnBar.style.display = 'none'; } },
      }),
    );
    this.warnBar.style.display = '';
  }

  /** 영상별 저장 오프셋을 UI에 반영 (설정 전역값과 분리된 per-video 상태) */
  setOffsetValue(offsetSec: number): void {
    this.offsetSec = offsetSec;
    this.updateOffsetLabel();
  }

  /** 전사 진행 칩 — null이면 숨김. 패널 본문을 점유하지 않는 작은 표시.
   *  cancellable이면 ✕ 버튼으로 진행 중인 전사를 취소할 수 있다 (현재 영상 잡만). */
  setGenerationChip(text: string | null, cancellable = false): void {
    if (!text) {
      this.genChip.style.display = 'none';
      this.genList.style.display = 'none';
      return;
    }
    this.genChip.replaceChildren(icon(ICONS.sparkle), text);
    if (cancellable) {
      this.genChip.append(h('button', {
        className: 'ey-gen-chip-cancel',
        text: '×',
        title: '전사 취소',
        on: {
          click: (e) => {
            e.stopPropagation(); // 칩의 대기열 목록 토글로 새지 않게
            if (window.confirm('진행 중인 전사를 취소할까요?')) this.callbacks.onCancelGenerate();
          },
        },
      }));
    }
    this.genChip.style.display = '';
    this.renderGenList();
  }

  /**
   * 한 줄 알림 칩 — null이면 숨김.
   *
   * 쓰는 곳: 커버 자동 연결("동일 곡 추정 — 자동 연결 확인 중" → "자동 연결됨"),
   * 붙여넣기 표기 필터 결과. 패널 본문을 점유하지 않아 어떤 화면 위에서도 뜬다.
   * autoHideMs를 주면 그 뒤 스스로 사라진다 (마지막 호출이 이긴다 — 알림은 상태가
   * 아니라 사건이므로 겹치면 새 소식을 보여주는 편이 맞다).
   */
  setNoticeChip(text: string | null, autoHideMs?: number): void {
    clearTimeout(this.noticeTimer);
    if (!text) {
      this.noticeChip.style.display = 'none';
      this.noticeChip.replaceChildren();
      return;
    }
    this.noticeChip.replaceChildren(icon(ICONS.sparkle), text);
    this.noticeChip.title = text; // 칩이 좁아 잘려도 전문을 볼 수 있게
    this.noticeChip.style.display = '';
    if (autoHideMs !== undefined) {
      this.noticeTimer = window.setTimeout(() => {
        this.noticeChip.style.display = 'none';
        this.noticeChip.replaceChildren();
      }, autoHideMs);
    }
  }

  /** 내 생성 대기열 목록 데이터 갱신 — 진행 칩 클릭으로 펼친다.
   *  이 브라우저(activeJobs)가 시킨 잡만 들어오므로 타인 큐는 노출되지 않는다. */
  setGenerationList(items: { title: string; state: string; isCurrent: boolean }[]): void {
    this.genListItems = items;
    this.renderGenList();
  }

  private renderGenList(): void {
    const chipHidden = this.genChip.style.display === 'none';
    if (!this.genListOpen || chipHidden || this.genListItems.length === 0) {
      this.genList.style.display = 'none';
      return;
    }
    this.genList.replaceChildren(...this.genListItems.map((it) =>
      h('div', { className: `ey-gen-list-row${it.isCurrent ? ' current' : ''}` },
        h('span', {
          className: 'ey-gen-list-title',
          text: it.isCurrent ? `${it.title} (현재 영상)` : it.title,
          title: it.title,
        }),
        h('span', { className: 'ey-gen-list-state', text: it.state }),
      )));
    this.genList.style.display = '';
  }

  updateDebug(info: DebugInfo): void {
    if (!this.settings.debugInfo) return;
    const t = info.time === null ? '-' : `${info.time.toFixed(2)}s`;
    const off = `${info.offsetSec > 0 ? '+' : ''}${info.offsetSec.toFixed(1)}`;
    const line = info.lineCount > 0 ? `${info.lineIndex + 1}/${info.lineCount}` : '-';
    const video = info.videoInfo === 'none' ? 'none' : `${info.videoBound ? 'OK' : 'MISMATCH'}(${info.videoInfo})`;
    const g = info.confGrades;
    const diag = [
      // 사람이 읽는 등급 분포 (글자 색과 동일 기준: 좋음=초록, 보통=노랑, 낮음=빨강)
      g ? `정렬 좋음${Math.round(g.ok * 100)}%·보통${Math.round(g.mid * 100)}%·낮음${Math.round(g.low * 100)}%` : null,
      info.quality != null ? `conf=${fmtConf(info.quality)}` : null,
      info.qualityMed != null ? `med=${fmtConf(info.qualityMed)}` : null,
      info.alignmentText ? `전사텍스트=${info.alignmentText === 'pronunciation' ? '독음(한국어 발음)' : '원문(원어)'}` : null,
      info.zone ? `zone=${info.zone}` : null,
      info.lineDebug,
    ].filter(Boolean).join(' ');
    this.debugEl.textContent =
      `vid=${info.videoId ?? '-'} src=${info.source}${info.synced ? '/sync' : '/plain'} line=${line} pip=${info.pipOpen ? 'Y' : 'N'}\n`
      + `t=${t} off=${off} video=${video} eng=${info.engineRunning ? 'Y' : 'N'}${info.jobStatus ? ` ${info.jobStatus}` : ''}`
      + (diag ? `\n${diag}` : '');
  }

  applySettings(settings: Settings): void {
    this.settings = settings;
    this.panel.classList.remove('ey-fs-small', 'ey-fs-medium', 'ey-fs-large');
    this.panel.classList.add(`ey-fs-${settings.fontSize}`);
    // 테마 판정은 lib/theme.ts 한 곳에서만 — PiP도 content가 같은 값을 받아 칠한다
    this.panel.classList.toggle('ey-light', resolveTheme(settings) === 'light');
    // 오프셋은 영상별 상태(setOffsetValue로 주입) — 전역 설정으로 되돌리지 않는다
    this.debugEl.style.display = settings.debugInfo ? '' : 'none';
    this.panel.classList.toggle('ey-hide-pron', !settings.showPronunciation);
    // 디버그 모드에서 글자별 CTC 신뢰도를 색으로 표시
    this.panel.classList.toggle('ey-show-conf', settings.debugInfo);
    // 디버그 토글은 서버 요청 로그의 노출 조건이기도 하다 — 배너를 다시 그려 반영
    this.renderServerBar();
  }

  // ── 내부 헬퍼 ─────────────────────────────────────────────────

  private headerButton(svg: string, title: string, onClick: () => void): HTMLButtonElement {
    return h('button', { className: 'ey-btn', title, on: { click: onClick } }, icon(svg));
  }

  private footerButton(text: string, title: string, onClick: () => void): HTMLButtonElement {
    return h('button', { className: 'ey-offset-btn', text, title, on: { click: onClick } });
  }

  private makeGenerateButton(label: string, onClick: () => void): HTMLButtonElement {
    const btn = createGenerateButton(label, this.serverStatus, onClick);
    this.generateButtons.push(btn);
    return btn;
  }

  private resetBody(): void {
    this.body.replaceChildren();
    this.banner.style.display = 'none';
    this.footer.style.display = 'none';
    this.resumeChip.style.display = 'none';
    this.pipBtn.style.display = 'none';
    this.regenBtn.style.display = 'none';
    this.lines = [];
    this.lineEls = [];
    this.activeWordEls = [];
    this.currentIndex = -1;
    this.userScrollUntil = 0;
    this.progressBar = null;
    this.progressText = null;
    this.searchResultsEl = null;
    this.closeSettings();
  }

  private showBanner(text: string, action?: HTMLElement): void {
    this.banner.replaceChildren(h('span', { className: 'ey-banner-text', text }));
    if (action) this.banner.append(action);
    this.banner.style.display = '';
  }

  private setSourceBadge(source: LyricsSource, synced: boolean): void {
    const base = source === 'everyric' ? 'Everyric'
      : source === 'vocaro' ? '보카로 가사 위키'
      : source === 'caption' ? '유튜브 자막'
      : 'LRCLIB';
    // 가사 원출처(위키 등)를 병기 — 전사는 서버가 했어도 가사의 출처는 따로 표기
    const extra = this.attributionName && this.attributionName !== base ? ` · ${this.attributionName}` : '';
    // 다른 영상의 싱크를 빌려온 경우 링크 표시 (해제는 검색 시트에서).
    // 검증(반주 대조)을 통과한 자동 링크와 검증 없는 수동 링크는 신뢰도가 다르다 —
    // 어긋난 가사를 보고 있을 때 원인을 짚을 수 있도록 ✓/? 로 구분해 표시한다
    const link = this.linkedInfo
      ? ` · 🔗${this.linkedInfo.verified ? '✓' : '?'}${this.linkedInfo.offsetSec !== 0 ? `${this.linkedInfo.offsetSec > 0 ? '+' : ''}${this.linkedInfo.offsetSec}s` : ''}`
      : '';
    this.sourceBadge.textContent = base + extra + link;
    // 출처 상세: 무엇을 어디서 가져왔는지 — 클릭 전에 툴팁으로도 확인 가능
    const kind = synced ? '싱크 가사' : '일반 가사';
    this.sourceBadge.title = this.sourceUrl ? `${kind} · 출처 페이지 열기\n${this.sourceUrl}` : kind;
    if (this.linkedInfo) this.sourceBadge.title += `\n${this.describeLink(this.linkedInfo)}`;
    this.sourceBadge.classList.toggle('everyric', source === 'everyric');
  }

  /**
   * 링크 한 건을 사람이 읽는 한 줄로 — 배지 툴팁과 검색 시트가 같은 문구를 쓴다.
   *
   * verified가 undefined면 서버가 검증 여부를 안 내려준 구버전이다 — 단정하지 않는다
   * (검증됐다고 잘못 말하면 어긋난 싱크를 신뢰하게 된다).
   */
  private describeLink(info: { sourceVideoId: string; verified?: boolean }): string {
    if (info.verified === true) return `${info.sourceVideoId} 싱크를 빌려옴 · 반주 대조로 검증된 자동 연결`;
    if (info.verified === false) {
      return `${info.sourceVideoId} 싱크를 빌려옴 · 검증 없는 수동 연결 — 어긋나면 오프셋을 조정하거나 해제하세요`;
    }
    return `${info.sourceVideoId} 싱크를 빌려옴 · 검증 여부 알 수 없음(구버전 서버)`;
  }

  /** 가사 원출처 표기 (이름+링크). show* 호출 전에 설정해야 배지에 반영된다. */
  setAttribution(attr: { name: string; url?: string | null } | null): void {
    this.attributionName = attr?.name ?? null;
    this.setSourceUrl(attr?.url ?? null);
  }

  /** 출처 페이지 링크 (보카로 위키 등 CC BY 출처 표기) — null이면 배지는 단순 라벨 */
  setSourceUrl(url: string | null): void {
    this.sourceUrl = url;
    this.sourceBadge.classList.toggle('link', url !== null);
    if (url) this.sourceBadge.title = '출처 페이지 열기';
  }

  private changeOffset(delta: number | null): void {
    const next = delta === null ? 0 : Math.round((this.offsetSec + delta) * 10) / 10;
    this.offsetSec = next;
    this.updateOffsetLabel();
    this.callbacks.onOffsetChange(next);
  }

  private updateOffsetLabel(): void {
    const v = this.offsetSec;
    this.offsetLabel.textContent = `${v > 0 ? '+' : ''}${v.toFixed(1)}s`;
    this.offsetLabel.classList.toggle('nonzero', v !== 0);
  }

  private markUserScroll(): void {
    if (this.stateKind !== 'synced') return;
    this.userScrollUntil = Date.now() + USER_SCROLL_HOLD_MS;
    if (this.currentIndex >= 0) this.resumeChip.style.display = '';
  }

  private resumeAutoScroll(): void {
    this.userScrollUntil = 0;
    this.resumeChip.style.display = 'none';
    this.scrollToCurrent();
  }

  private scrollToCurrent(): void {
    const el = this.currentIndex >= 0 ? this.lineEls[this.currentIndex] : undefined;
    if (!el) return;
    const top = el.offsetTop - this.body.clientHeight / 2 + el.offsetHeight / 2;
    this.body.scrollTo({ top, behavior: 'smooth' });
    this.resumeChip.style.display = 'none';
  }

  // ── 설정 시트 ─────────────────────────────────────────────────

  private toggleSettings(): void {
    if (this.settingsSheet) {
      this.closeSettings();
      return;
    }
    this.openSettings();
  }

  /** 설정 시트 열기 — 서버 오류 배너의 '설정 열기'가 여기로 온다 (패널이 숨어 있으면 함께 띄운다) */
  openSettings(): void {
    if (!this.visible) this.setVisible(true);
    if (this.geometry.collapsed) this.setCollapsed(false);
    if (this.settingsSheet) return;
    const sheet = this.buildSettingsSheet();
    this.settingsSheet = sheet;
    this.panel.append(sheet);
  }

  private closeSettings(): void {
    this.settingsSheet?.remove();
    this.settingsSheet = null;
    this.settingsDot = null;
  }

  private buildSettingsSheet(): HTMLDivElement {
    const autoSearch = h('input', { attrs: { type: 'checkbox' } });
    autoSearch.checked = this.settings.autoSearch;
    autoSearch.addEventListener('change', () => this.callbacks.onSettingsChange({ autoSearch: autoSearch.checked }));

    const autoSearchShorts = h('input', { attrs: { type: 'checkbox' } });
    autoSearchShorts.checked = this.settings.autoSearchShorts;
    autoSearchShorts.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ autoSearchShorts: autoSearchShorts.checked }));

    const fontSelect = this.buildSelect(
      [['small', '작게'], ['medium', '보통'], ['large', '크게']],
      this.settings.fontSize,
      v => this.callbacks.onSettingsChange({ fontSize: v as Settings['fontSize'] }),
    );
    const themeSelect = this.buildSelect(
      [['auto', '자동'], ['dark', '다크'], ['light', '라이트']],
      this.settings.theme,
      v => this.callbacks.onSettingsChange({ theme: v as Settings['theme'] }),
    );

    const showTranslation = h('input', { attrs: { type: 'checkbox' } });
    showTranslation.checked = this.settings.showTranslation;
    showTranslation.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ showTranslation: showTranslation.checked }));

    const langSelect = this.buildSelect(
      [['ko', '한국어'], ['en', 'English'], ['ja', '日本語'], ['zh', '中文']],
      this.settings.translationLanguage,
      v => this.callbacks.onSettingsChange({ translationLanguage: v }),
    );

    const showPronunciation = h('input', { attrs: { type: 'checkbox' } });
    showPronunciation.checked = this.settings.showPronunciation;
    showPronunciation.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ showPronunciation: showPronunciation.checked }));

    const sourcePriority = this.buildSelect(
      [['vocaro', '보카로 위키 우선'], ['lrclib', 'LRCLIB 우선']],
      this.settings.lyricsSourcePriority,
      v => this.callbacks.onSettingsChange({ lyricsSourcePriority: v as Settings['lyricsSourcePriority'] }),
    );

    const pipKeepPanel = h('input', { attrs: { type: 'checkbox' } });
    pipKeepPanel.checked = this.settings.pipKeepPanel;
    pipKeepPanel.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ pipKeepPanel: pipKeepPanel.checked }));

    const pipShowVideo = h('input', { attrs: { type: 'checkbox' } });
    pipShowVideo.checked = this.settings.pipShowVideo;
    pipShowVideo.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ pipShowVideo: pipShowVideo.checked }));

    const pitchGuide = h('input', { attrs: { type: 'checkbox' } });
    pitchGuide.checked = this.settings.pitchGuide;
    pitchGuide.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ pitchGuide: pitchGuide.checked }));

    const pitchWindow = this.buildSelect(
      [['0.5', '½마디'], ['1', '1마디'], ['2', '2마디'], ['4', '4마디'], ['8', '8마디']],
      String(this.settings.pitchWindowMeasures),
      v => this.callbacks.onSettingsChange({ pitchWindowMeasures: Number(v) }),
    );

    const pitchMode = this.buildSelect(
      [['page', '고정 화면·헤드 이동'], ['scroll', '스크롤·헤드 고정']],
      this.settings.pitchScrollMode,
      v => this.callbacks.onSettingsChange({ pitchScrollMode: v as Settings['pitchScrollMode'] }),
    );

    const pitchFont = this.buildSelect(
      [['1', '보통'], ['1.2', '크게'], ['1.45', '아주 크게'], ['0.85', '작게']],
      String(this.settings.pitchFontScale),
      v => this.callbacks.onSettingsChange({ pitchFontScale: Number(v) }),
    );

    const pitchCountdown = h('input', { attrs: { type: 'checkbox' } });
    pitchCountdown.checked = this.settings.pitchCountdown;
    pitchCountdown.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ pitchCountdown: pitchCountdown.checked }));

    const pitchF0Curve = h('input', { attrs: { type: 'checkbox' } });
    pitchF0Curve.checked = this.settings.pitchF0Curve;
    pitchF0Curve.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ pitchF0Curve: pitchF0Curve.checked }));

    const pitchPronPosition = this.buildSelect(
      [['note', '노트 위'], ['bottom', '화면 하단']],
      this.settings.pitchPronPosition,
      v => this.callbacks.onSettingsChange({ pitchPronPosition: v as Settings['pitchPronPosition'] }),
    );

    const melodyPlayback = h('input', { attrs: { type: 'checkbox' } });
    melodyPlayback.checked = this.settings.melodyPlayback;
    melodyPlayback.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ melodyPlayback: melodyPlayback.checked }));
    const melodyVolume = this.buildRange(this.settings.melodyVolume, v =>
      this.callbacks.onSettingsChange({ melodyVolume: v }));

    const metronome = h('input', { attrs: { type: 'checkbox' } });
    metronome.checked = this.settings.metronome;
    metronome.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ metronome: metronome.checked }));
    const metronomeVolume = this.buildRange(this.settings.metronomeVolume, v =>
      this.callbacks.onSettingsChange({ metronomeVolume: v }));
    const metronomeRate = this.buildSelect(
      [['0.5', '½× (2분음표)'], ['1', '1× (4분음표)'], ['2', '2× (8분음표)']],
      String(this.settings.metronomeRate),
      v => this.callbacks.onSettingsChange({ metronomeRate: Number(v) }),
    );
    const metronomeBeat = this.buildSelect(
      [['0', '1박'], ['1', '2박'], ['2', '3박'], ['3', '4박']],
      String(this.settings.metronomeBeat),
      v => this.callbacks.onSettingsChange({ metronomeBeat: Number(v) }),
    );

    const micPitch = h('input', { attrs: { type: 'checkbox' } });
    micPitch.checked = this.settings.micPitch;
    micPitch.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ micPitch: micPitch.checked }));
    const micOctave = this.buildSelect(
      [['-2', '-2옥타브'], ['-1', '-1옥타브'], ['0', '보정 없음'], ['1', '+1옥타브'], ['2', '+2옥타브']],
      String(this.settings.micOctave),
      v => this.callbacks.onSettingsChange({ micOctave: Number(v) }),
    );

    const audioOut = h('select', { className: 'ey-select' });
    audioOut.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ audioOutputId: audioOut.value }));
    const micDevice = h('select', { className: 'ey-select' });
    micDevice.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ micDeviceId: micDevice.value }));
    void this.populateAudioDevices(audioOut, micDevice);

    const lowConfWarning = h('input', { attrs: { type: 'checkbox' } });
    lowConfWarning.checked = this.settings.lowConfWarning;
    lowConfWarning.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ lowConfWarning: lowConfWarning.checked }));

    const notifyOnComplete = h('input', { attrs: { type: 'checkbox' } });
    notifyOnComplete.checked = this.settings.notifyOnComplete;
    notifyOnComplete.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ notifyOnComplete: notifyOnComplete.checked }));

    const debugInfo = h('input', { attrs: { type: 'checkbox' } });
    debugInfo.checked = this.settings.debugInfo;
    debugInfo.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ debugInfo: debugInfo.checked }));

    const serverInput = h('input', { className: 'ey-input' });
    serverInput.value = this.settings.serverUrl;
    serverInput.addEventListener('change', () => {
      const url = serverInput.value.trim().replace(/\/+$/, '');
      if (url) this.callbacks.onSettingsChange({ serverUrl: url });
    });
    // 점 색만으론 "왜 빨간지"를 알 수 없다 — 사유를 툴팁으로 붙이고, 인증 실패는 따로 표시
    const dot = h('span', { className: 'ey-dot', title: `서버 연결 상태 — ${statusLine(this.serverStatus)}` });
    dot.classList.toggle('ok', this.serverStatus.kind === 'ok');
    dot.classList.toggle('auth', this.serverStatus.kind === 'auth');
    this.settingsDot = dot;
    // 서버가 정상이 아니면 설정 안에서도 사유를 글자로 남긴다 (색맹·툴팁 미표시 환경 대비)
    const serverNote = h('div', { className: 'ey-settings-note ey-settings-server-note' });
    if (!serverUsable(this.serverStatus)) {
      serverNote.textContent = statusLine(this.serverStatus)
        + (this.serverStatus.detail ? ` — ${this.serverStatus.detail}` : '');
      serverNote.classList.add('bad');
    } else {
      serverNote.style.display = 'none';
    }

    const apiKeyInput = h('input', { className: 'ey-input', attrs: { type: 'password', placeholder: '(선택) 서버 API 키' } });
    apiKeyInput.value = this.settings.apiKey;
    apiKeyInput.addEventListener('change', () =>
      this.callbacks.onSettingsChange({ apiKey: apiKeyInput.value.trim() }));

    return h('div', { className: 'ey-settings' },
      h('div', { className: 'ey-settings-row' }, h('label', { text: '자동 가사 검색 (음악 영상만)', attrs: { title: '유튜브 음악 메타·채널·제목으로 음악 영상을 판별해 자동으로 가사창을 엽니다. 꺼도 툴바 아이콘으로 수동으로 열 수 있어요.' } }), autoSearch),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '쇼츠에서도 자동 검색', attrs: { title: '기본은 꺼짐 — 쇼츠에서는 가사창이 자동으로 열리지 않아요. 툴바 아이콘으로 수동으로는 언제든 열 수 있어요.' } }), autoSearchShorts),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '폰트 크기' }), fontSelect),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '테마' }), themeSelect),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '가사 번역 표시' }), showTranslation),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '번역 언어' }), langSelect),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '발음 표기 표시 (있을 때)' }), showPronunciation),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '가사 소스 우선순위' }), sourcePriority),
      h('div', { className: 'ey-settings-row' }, h('label', { text: 'PiP 중에도 패널 가사 유지' }), pipKeepPanel),
      h('div', { className: 'ey-settings-row' }, h('label', { text: 'PiP에 영상 함께 표시' }), pipShowVideo),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '가라오케 음정 바 (BETA · PiP)' }), pitchGuide),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '음정 바 표시 구간' }), pitchWindow),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '음정 바 진행 방식' }), pitchMode),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '음정 바 글자 크기' }), pitchFont),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '가사 시작 카운트다운' }), pitchCountdown),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '음정 원본 곡선(f0) 표시', attrs: { title: '음정 모델이 추출한 원본 멜로디 곡선을 레인에 파란 선으로 표시합니다. 디버그 모드와 무관하게 켜고 끌 수 있어요.' } }), pitchF0Curve),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '발음 표기 위치', attrs: { title: '음절 타이밍이 있는 곡에서 발음 표기를 어디에 표시할지 고릅니다. 노트 위 = 각 노트 바로 아래 부착, 화면 하단 = 진행률 그라데이션으로 하단 중앙 표시.' } }), pitchPronPosition),
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '멜로디 재생 (가라오케 창)', attrs: { title: '전사된 노트를 신디사이즈로 재생합니다. 가라오케 창이 열려 있을 때만 소리가 나요.' } }),
        h('span', { className: 'ey-settings-inline' }, melodyVolume, melodyPlayback)),
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '메트로놈', attrs: { title: '서버가 추정한 BPM에 맞춰 박자를 칩니다 (4/4 가정, 4박마다 강세).' } }),
        h('span', { className: 'ey-settings-inline' }, metronomeVolume, metronome)),
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '메트로놈 배속', attrs: { title: '느리게 느껴지는 곡은 2×(8분음표), 너무 빠른 곡은 ½×로. 가라오케 창 안 버튼으로도 바꿀 수 있어요.' } }), metronomeRate),
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '마디 시작 박', attrs: { title: '곡의 첫 강세가 안 맞을 때 마디 시작 박을 옮깁니다. 메트로놈 강세와 레인 마디선이 함께 이동해요.' } }), metronomeBeat),
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '가라오케 오디오 출력 기기', attrs: { title: '멜로디·메트로놈만 이 기기로 나갑니다. 영상 소리는 기존 출력 그대로.' } }), audioOut),
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '마이크 음정 표시 (레인)', attrs: { title: '마이크로 부른 음정을 가라오케 레인에 청록 궤적으로 표시합니다. 켜면 마이크 권한을 요청해요.' } }), micPitch),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '마이크 기기' }), micDevice),
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '마이크 옥타브 보정', attrs: { title: '마이크 궤적이 노트보다 한 옥타브 위/아래로 그려지면 여기서 보정하세요.' } }), micOctave),
      h('div', { className: 'ey-settings-note', text: '기기 이름은 마이크 권한을 한 번 허용해야 표시돼요' }),
      h('div', { className: 'ey-settings-row ey-settings-col' },
        h('label', {}, '싱크 서버 URL ', dot),
        serverInput,
      ),
      h('div', { className: 'ey-settings-row ey-settings-col' },
        h('label', { text: 'API 키' }),
        apiKeyInput,
      ),
      serverNote,
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '낮은 정렬 신뢰도 경고', attrs: { title: '전사 신뢰도가 매우 낮은 곡에서 가사창 상단에 경고 바를 띄웁니다.' } }), lowConfWarning),
      h('div', { className: 'ey-settings-row' },
        h('label', { text: '전사 완료 알림', attrs: { title: '대기열에 넣은 전사가 끝나면 브라우저 알림으로 알려줍니다. 다른 탭에 있어도 확인할 수 있어요.' } }), notifyOnComplete),
      h('div', { className: 'ey-settings-row' }, h('label', { text: '디버그 정보 표시' }), debugInfo),
      h('div', { className: 'ey-settings-note', text: '싱크 생성·번역은 Everyric 서버가 필요해요' }),
      h('button', { className: 'ey-secondary-btn', text: '닫기', on: { click: () => this.closeSettings() } }),
    );
  }

  private buildRange(value: number, onChange: (v: number) => void): HTMLInputElement {
    const range = h('input', {
      className: 'ey-settings-range',
      attrs: { type: 'range', min: '0', max: '100', step: '1', value: String(Math.round(value * 100)) },
    });
    range.addEventListener('change', () => onChange(Number(range.value) / 100));
    return range;
  }

  /** 오디오 입출력 기기 목록 채우기 — 라벨은 마이크 권한을 허용해야 브라우저가 내려준다 */
  private async populateAudioDevices(outSel: HTMLSelectElement, inSel: HTMLSelectElement): Promise<void> {
    const fill = (sel: HTMLSelectElement, devices: MediaDeviceInfo[], defLabel: string, cur: string) => {
      sel.replaceChildren(h('option', { text: defLabel, attrs: { value: '' } }));
      devices.forEach((d, i) => {
        if (!d.deviceId || d.deviceId === 'default' || d.deviceId === 'communications') return;
        sel.append(h('option', { text: d.label || `기기 ${i + 1}`, attrs: { value: d.deviceId } }));
      });
      sel.value = Array.from(sel.options).some(o => o.value === cur) ? cur : '';
    };
    let devices: MediaDeviceInfo[] = [];
    try {
      devices = await navigator.mediaDevices.enumerateDevices();
    } catch {
      /* 권한 API 불가 환경 — 기본 항목만 표시 */
    }
    fill(outSel, devices.filter(d => d.kind === 'audiooutput'), '기본 출력', this.settings.audioOutputId);
    fill(inSel, devices.filter(d => d.kind === 'audioinput'), '기본 마이크', this.settings.micDeviceId);
  }

  private buildSelect(options: [string, string][], value: string, onChange: (v: string) => void): HTMLSelectElement {
    const select = h('select', { className: 'ey-select' });
    for (const [v, label] of options) {
      const opt = h('option', { text: label, attrs: { value: v } });
      select.append(opt);
    }
    select.value = value;
    select.addEventListener('change', () => onChange(select.value));
    return select;
  }

  // ── 위치/크기 ─────────────────────────────────────────────────

  private defaultGeometry(): PanelGeometry {
    return {
      x: Math.max(EDGE_MARGIN, window.innerWidth - DEFAULT_WIDTH - 24),
      y: 72,
      width: DEFAULT_WIDTH,
      height: Math.min(DEFAULT_HEIGHT, Math.round(window.innerHeight * 0.7)),
      collapsed: false,
    };
  }

  private applyGeometry(): void {
    this.applyingGeometry = true;
    const g = this.geometry;
    this.panel.style.left = `${g.x}px`;
    this.panel.style.top = `${g.y}px`;
    this.panel.style.width = `${g.width}px`;
    this.panel.classList.toggle('collapsed', g.collapsed);
    this.panel.style.height = g.collapsed ? 'auto' : `${g.height}px`;
    this.collapseBtn.replaceChildren(icon(g.collapsed ? ICONS.expand : ICONS.collapse));
    this.collapseBtn.title = g.collapsed ? '펼치기' : '접기';
    requestAnimationFrame(() => {
      this.applyingGeometry = false;
    });
  }

  private setCollapsed(collapsed: boolean): void {
    this.geometry.collapsed = collapsed;
    this.applyGeometry();
    this.scheduleGeometrySave();
  }

  private setupDrag(): void {
    let startX = 0;
    let startY = 0;
    let origX = 0;
    let origY = 0;
    let dragging = false;

    this.header.addEventListener('pointerdown', (e: PointerEvent) => {
      if ((e.target as HTMLElement).closest('button')) return;
      dragging = true;
      startX = e.clientX;
      startY = e.clientY;
      origX = this.geometry.x;
      origY = this.geometry.y;
      this.header.setPointerCapture(e.pointerId);
    });
    this.header.addEventListener('pointermove', (e: PointerEvent) => {
      if (!dragging) return;
      this.geometry.x = this.clampX(origX + e.clientX - startX);
      this.geometry.y = this.clampY(origY + e.clientY - startY);
      this.panel.style.left = `${this.geometry.x}px`;
      this.panel.style.top = `${this.geometry.y}px`;
    });
    this.header.addEventListener('pointerup', (e: PointerEvent) => {
      if (!dragging) return;
      dragging = false;
      this.header.releasePointerCapture(e.pointerId);
      this.scheduleGeometrySave();
    });
    this.header.addEventListener('dblclick', () => this.setCollapsed(!this.geometry.collapsed));
  }

  private handlePanelResize(): void {
    if (this.applyingGeometry || this.geometry.collapsed) return;
    const { offsetWidth, offsetHeight } = this.panel;
    if (offsetWidth === this.geometry.width && offsetHeight === this.geometry.height) return;
    this.geometry.width = offsetWidth;
    this.geometry.height = offsetHeight;
    this.scheduleGeometrySave();
  }

  private handleWindowResize = (): void => {
    this.geometry.x = this.clampX(this.geometry.x);
    this.geometry.y = this.clampY(this.geometry.y);
    this.panel.style.left = `${this.geometry.x}px`;
    this.panel.style.top = `${this.geometry.y}px`;
  };

  private handleFullscreenChange = (): void => {
    this.fullscreenHidden = document.fullscreenElement !== null;
    this.updateHostVisibility();
  };

  private clampX(x: number): number {
    return Math.min(Math.max(x, EDGE_MARGIN), Math.max(EDGE_MARGIN, window.innerWidth - this.geometry.width - EDGE_MARGIN));
  }

  private clampY(y: number): number {
    return Math.min(Math.max(y, EDGE_MARGIN), Math.max(EDGE_MARGIN, window.innerHeight - 48));
  }

  private updateHostVisibility(): void {
    this.host.style.display = this.visible && !this.fullscreenHidden ? '' : 'none';
  }

  private scheduleGeometrySave(): void {
    clearTimeout(this.saveGeomTimer);
    this.saveGeomTimer = window.setTimeout(() => {
      this.callbacks.onGeometryChange({ ...this.geometry });
    }, 400);
  }
}
