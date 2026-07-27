import { detectSong, getCurrentVideoId, getVideoElement } from './lib/song-detector';
import { SyncEngine, type SyncHandlers } from './lib/sync-engine';
import { KaraokeAudio, collectMelodyNotes } from './lib/karaoke-audio';
import { parseTriLineLyrics } from './lib/tri-line';
import { describeRemoved, stripPartMarkers } from './lib/lyrics-clean';
import { MicPitch } from './lib/mic-pitch';
import { getGeometry, getSettings, saveGeometry, saveSettings } from './lib/settings';
import { resolveScript } from './lib/lang';
import { setUiLanguage, t } from './lib/i18n';
import { LyricsOverlay } from './ui/overlay';
import { PipController } from './ui/pip';
import {
  captionSourceLabel,
  getCaptionTracks,
  mergeCaptionTranslation,
  selectLyricTrack,
  selectTranslationTrack,
} from './lib/yt-captions';
import type {
  ApiFailure,
  BgRequest,
  CaptionLine,
  ContentMessage,
  GenerateResponse,
  JobStatusResponse,
  LinkCandidatesResponse,
  LinkJobStatusResponse,
  LyricLine,
  LyricsData,
  MessageResponse,
  PanelGeometry,
  LineMeta,
  SearchCandidate,
  ServerLogEntry,
  ServerStatus,
  Settings,
  SongInfo,
  SourceAttribution,
  SyncListItem,
  TranslateResult,
  TranslatedLine,
} from './types';
import { affectsServerStatus, failureToStatus, serverKnownBad, statusLine, unknownStatus } from './lib/server-status';
import { resolveTheme } from './lib/theme';
import type { SourceResult } from './lib/sources';
import type { VocaroLine, VocaroResult } from './lib/vocaro';

let settings: Settings;
let cssText = '';
let initialGeometry: PanelGeometry | null = null;
let overlay: LyricsOverlay | null = null;
const pip = new PipController();
const engine = new SyncEngine();
const karaokeAudio = new KaraokeAudio(() => engine.getVideo() ?? getVideoElement());
const micPitch = new MicPitch();

let currentVideoId: string | null = null;
let currentSong: SongInfo | null = null;
let currentData: LyricsData | null = null;
let currentSourceUrl: string | null = null; // 보카로 위키 출처 페이지 (CC BY 출처 표기용)
let lastVocaro: { videoId: string; lines: VocaroLine[] } | null = null; // 싱크 생성 후 발음/번역 재병합용
/**
 * 싱크 초기화 직전의 가사 — 초기화는 **타이밍을 버리는 것**이고 원문을 버리는 것이 아니다.
 *
 * 남기지 않으면 초기화 직후 재조회가 서버 미스로 떨어져 자막 폴백이 화면을 채운다. 실제로
 * 그 자막(자동 생성)이 정상 가사를 대체하고, 거기서 생성을 누르니 ASR 전사가 영구 싱크로
 * 저장됐다(aDnGs2i_qqo). 초기화한 사용자의 의도는 "이 가사로 다시 만들자"이므로, 방금
 * 지운 가사를 화면에 남겨 그대로 재생성할 수 있게 한다.
 */
let keptLyrics: { videoId: string; data: LyricsData } | null = null;
/** 진행 중인 전사 잡 — videoId 키. 영상을 이동해도 백그라운드로 계속 추적한다 */
const generatingJobs = new Map<string, {
  jobId: string; progress: number; queueLabel?: string;
  stage?: string; stageProgress?: number; title?: string;
}>();
// 생성 요청 준비 단계(LLM 번역·독음 대기, 수십 초) 중인 영상 — 잡 등록 전이라
// generatingJobs가 비어 있어, 이 가드가 없으면 버튼 연타가 전부 서버로 나간다
const preparingGenerate = new Set<string>();
// 진행 중 잡을 탭 간 공유하는 storage 키 — 다른 탭/새 탭에서도 진행 칩이 이어진다
const JOBS_STORAGE_KEY = 'activeJobs';
/**
 * 진행 중인 커버 자동 연결(반주 상관 검증) 잡 — videoId 키.
 *
 * 전사 잡(generatingJobs)과 달리 storage로 탭 간 공유하지 않는다: 검증은 사용자가 시킨
 * 작업이 아니라 서버가 알아서 낸 것이고(사용자에게 진행 책임이 없다), 잃어도 다음에 그
 * 영상을 열 때 서버가 다시 판단한다. 세션 안에서만 추적해 배지를 띄우면 충분하다.
 */
const linkJobs = new Map<string, { linkJobId: string; title?: string; started: number }>();
/** 검증 잡을 이 시간까지만 지켜본다 — 워커가 비어 큐에 계속 머무는 잡을 무한 폴링하지 않기
 *  위한 상한. 포기해도 서버 작업은 계속되며, 다음에 그 영상을 열면 링크가 이미 반영돼 있다. */
const LINK_JOB_WATCH_MS = 10 * 60 * 1000;
/** 이 세션에서 후보 탐색을 이미 물어본 영상 — 같은 영상을 다시 열 때 중복 질의를 막는다 */
const linkProbed = new Set<string>();
// t()는 uiLanguage가 바뀔 수 있으므로 모듈 상수로 얼리지 않고 쓰는 시점에 매번 부른다
/** 알림 칩이 현재 어느 영상의 것인가 — 영상이 바뀔 때만 칩을 비우기 위한 표식 */
let noticeVideoId: string | null = null;
// 현재 영상의 사용자 싱크 오프셋(초) — 영상마다 서버에 저장·복원된다 (전역 설정 아님)
let videoOffset = 0;
let offsetSaveTimer: number | undefined;
let pollTimer: number | undefined;
let searchSeq = 0;
let lastLineIndex = -1;
let lastDebugPush = 0;
const translationCache = new Map<string, TranslatedLine[]>(); // `${videoId}:${lang}` → 라인별 번역+발음
// 같은 곡의 번역 요청이 동시에 여러 갈래(표시 경로·생성 경로)에서 뜨면 하나로 합친다 —
// LLM 호출은 수십 초짜리라 중복이 곧 서버 스레드 낭비 + 진행 지연이다
const pendingTranslate = new Map<string, Promise<TranslatedLine[] | undefined>>();

async function init(): Promise<void> {
  settings = await getSettings();
  setUiLanguage(settings.uiLanguage); // 이 콘텐츠 스크립트가 실제로 t()를 쓰는 유일한 곳 — 세션 시작 시 한 번 맞춘다
  [cssText, initialGeometry] = await Promise.all([loadCss(), getGeometry()]);
  chrome.runtime.onMessage.addListener(handleRuntimeMessage);
  await restoreActiveJobs();
  watchJobsFromOtherTabs();
  observeNavigation();
  checkCurrentPage();
}

/** 다른 탭이 새로 시작한 전사 잡을 실시간으로 이어받는다 (storage 이벤트).
 *  삭제 동기화는 하지 않는다 — 다른 탭이 마감한 잡은 이 탭 폴링도 곧
 *  completed/404를 보고 스스로 정리하므로, 이벤트 순서 경합으로 산 잡을
 *  잘못 지우는 위험만 남기 때문. */
function watchJobsFromOtherTabs(): void {
  try {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'local' || !changes[JOBS_STORAGE_KEY]) return;
      const jobs = (changes[JOBS_STORAGE_KEY].newValue ?? {}) as
        Record<string, { jobId: string; title?: string }>;
      let added = false;
      for (const [videoId, job] of Object.entries(jobs)) {
        if (job?.jobId && !generatingJobs.has(videoId)) {
          generatingJobs.set(videoId, { jobId: job.jobId, progress: 0, title: job.title });
          added = true;
        }
      }
      if (added) {
        ensurePolling();
        updateGenChip();
      }
    });
  } catch { /* 이벤트 미지원 환경 — 이 탭에서 시작한 잡만 추적 */ }
}

/** 다른 탭(또는 이전 세션)이 시작한 전사 잡을 이어받아 진행 칩·폴링을 계속한다 */
async function restoreActiveJobs(): Promise<void> {
  try {
    const stored = await chrome.storage.local.get(JOBS_STORAGE_KEY);
    const jobs = stored[JOBS_STORAGE_KEY] as
      Record<string, { jobId: string; title?: string }> | undefined;
    if (!jobs) return;
    for (const [videoId, job] of Object.entries(jobs)) {
      if (job?.jobId && !generatingJobs.has(videoId)) {
        generatingJobs.set(videoId, { jobId: job.jobId, progress: 0, title: job.title });
      }
    }
    if (generatingJobs.size > 0) ensurePolling();
  } catch { /* storage 실패 — 이 탭에서 시작한 잡만 추적 */ }
}

// 이 탭이 마감(완료·실패·취소·교체)한 잡의 videoId — 병합 저장 때 이 항목만 걷어낸다
const finishedJobs = new Set<string>();

/** 잡 추적 종료 + storage 반영 — 삭제는 반드시 이 경로로 (병합 저장이 마감을 알아야 한다) */
function removeJob(videoId: string): void {
  if (generatingJobs.delete(videoId)) finishedJobs.add(videoId);
  void persistActiveJobs();
}

/** 진행 중 잡 목록을 storage에 반영 — 다른 탭이 이어받을 수 있게.
 *  통째로 덮어쓰면 탭끼리 서로의 잡을 지우므로 read-merge-write:
 *  이 탭의 잡은 얹고, 이 탭이 마감한 잡만 걷어낸다. */
async function persistActiveJobs(): Promise<void> {
  try {
    const stored = await chrome.storage.local.get(JOBS_STORAGE_KEY);
    const merged: Record<string, { jobId: string; title?: string }> = {
      ...(stored[JOBS_STORAGE_KEY] as Record<string, { jobId: string; title?: string }> | undefined),
    };
    for (const v of finishedJobs) delete merged[v];
    finishedJobs.clear();
    for (const [v, j] of generatingJobs) merged[v] = { jobId: j.jobId, title: j.title };
    await chrome.storage.local.set({ [JOBS_STORAGE_KEY]: merged });
  } catch { /* storage 실패는 치명적이지 않다 */ }
}

async function loadCss(): Promise<string> {
  try {
    const res = await fetch(chrome.runtime.getURL('overlay.css'));
    return await res.text();
  } catch {
    return '';
  }
}

function handleRuntimeMessage(message: ContentMessage): void {
  if (message.type === 'TOGGLE_OVERLAY') {
    void toggleOverlay();
  } else if (message.type === 'TOGGLE_DEBUG') {
    void toggleDebugInfo();
  } else if (message.type === 'SYNC_GENERATED' && message.payload.videoId === currentVideoId) {
    void searchLyrics();
  } else if (message.type === 'PERMISSIONS_CHANGED') {
    // 옵션 페이지(다른 탭)에서 로컬 서버 권한을 허용·철회했다 — 서버 상태는 그 권한을
    // 근거로 판정되므로 다시 물어야 배너가 사라지거나 나타난다.
    void refreshServerStatus();
  }
}

/**
 * 권한 관리 페이지를 연다 — `chrome.permissions.request()`가 여기서 안 되기 때문이다.
 *
 * 그 API는 확장 페이지·service worker에서만 쓸 수 있고(content script 불가), service
 * worker에서 request()를 부르면 사용자 제스처 컨텍스트가 없어 실패한다. 그래서 확장 페이지를
 * 하나 만들고, 그 페이지의 버튼 클릭을 제스처로 삼는다.
 *
 * 페이지를 못 열면 조용히 끝내지 않는다 — 그러면 버튼이 죽은 것처럼 보인다.
 */
async function openPermissionsPage(): Promise<void> {
  const res = await sendToBackground({ type: 'OPEN_OPTIONS' });
  if (res.error) {
    showNotice(t('content.notice.permPageFailed'), 20000);
  }
}

/**
 * 디버그 정보 표시를 핫키로 켜고 끈다 (Alt+Shift+D — 키 선택 근거는 background.ts).
 *
 * **켤 때는 패널도 함께 연다.** 디버그 줄은 패널 안에 있고(`ey-debug`) `pushDebug`가 패널이
 * 없으면 아무 것도 하지 않으므로, 패널이 닫힌 채로 켜면 눌렸는지조차 알 수 없다 — 핫키가
 * 조용히 아무 일도 안 하는 것처럼 보이는 것이 이 기능에서 가장 나쁜 결과다.
 *
 * **끌 때는 패널을 건드리지 않는다.** 디버그만 끄려던 사람의 패널을 닫아 버리면 곤란하다.
 *
 * 설정 변경은 `handleSettingsChange`를 그대로 탄다 — 저장·패널·PiP 반영이 한 곳에 모여 있어
 * 핫키 경로만 다르게 동작할 여지가 없다.
 */
async function toggleDebugInfo(): Promise<void> {
  const next = !settings.debugInfo;
  if (next) ensureOverlay().setVisible(true);
  await handleSettingsChange({ debugInfo: next });
}

function observeNavigation(): void {
  document.addEventListener('yt-navigate-finish', () => window.setTimeout(checkCurrentPage, 300));
  window.setInterval(checkCurrentPage, 1500);
  window.setInterval(watchVideoBinding, 3000);
  // 유튜브 다크모드 토글(html[dark])을 실시간 반영 — theme=auto일 때 패널·PiP·레인 색 갱신.
  // PiP는 유튜브 페이지 컨텍스트가 없어 스스로 판정할 수 없으므로 여기서 판정해 밀어넣는다.
  new MutationObserver(() => {
    if (settings.theme !== 'auto') return;
    overlay?.applySettings(settings);
    pip.setTheme(resolveTheme(settings)); // 레인 색 재판독은 setTheme이 함께 처리
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['dark'] });
}

/**
 * YouTube가 광고/프리뷰 등으로 video 엘리먼트를 교체하거나 엔진이 재생 중이
 * 아닌 video에 붙은 경우, 실제 재생 중인 video로 자동 재바인딩한다.
 * (증상: 가사는 뜨는데 하이라이트가 재생 시간을 따라오지 않음)
 */
function watchVideoBinding(): void {
  if (!currentData?.synced || !engine.isRunning()) return;
  // 내비게이션 직후엔 DOM은 새 영상, currentData는 이전 곡인 창이 있다 — 이전 곡을
  // 새 영상에 붙이지 않도록 페이지 videoId가 아직 같을 때만 재바인딩한다
  if (getCurrentVideoId() !== currentVideoId) return;
  const video = getVideoElement();
  if (video && video !== engine.getVideo()) {
    engine.start(video, currentData.lines, makeEngineHandlers());
    engine.setOffset(videoOffset);
    refreshPipMirror(video); // 미러 스트림도 새 video 기준으로 갱신
  }
}

/** 자동 검색이 꺼져 있으면 사용자가 패널을 열어둔 경우에만 따라간다.
 * 자동 검색이 켜져 있어도 음악 영상으로 판별될 때만 자동으로 뜬다 —
 * 브이로그/게임 영상에서 노래를 찾겠다고 패널이 뜨는 것을 막는다. */
function shouldFollow(): boolean {
  if (overlay?.isVisible()) return true; // 사용자가 열어둔 패널은 항상 따라간다
  // PiP가 열려 있다는 것만으로 추종하지는 않는다 — searchLyrics가 메인 패널을
  // setVisible(true)로 되살리므로, 사용자가 X로 닫은 패널이 곡을 넘길 때마다 다시 열린다.
  // 추종하지 않는 영상에서 이전 곡을 지우는 일은 checkCurrentPage가 따로 처리한다.
  //
  // 정정: 이 주석은 처음에 "브이로그로 넘어갈 때마다 닫은 패널이 되살아난다"를 이 조건의
  // 근거로 적었는데 **그 인과는 틀렸다.** 브라우저 검증에서 PiP를 열지 않은 대조군에서도
  // 같은 증상이 나왔고, 원인은 낡은 microdata였다(isLikelyMusicVideo 주석 참조).
  // 조건 자체는 유지한다 — 닫은 패널이 자동으로 되살아나는 것은 여전히 원치 않는 동작이다.
  if (!settings.autoSearch) return false;
  // 쇼츠는 기본적으로 자동으로 열지 않는다 (설정으로 허용 가능, 수동 열기는 그대로)
  if (!settings.autoSearchShorts && location.pathname.startsWith('/shorts/')) return false;
  return isLikelyMusicVideo();
}

/**
 * 이 페이지의 microdata가 **지금 이 영상의 것**인가.
 *
 * SPA 이동 후 microdata 블록은 통째로 이전 영상 값으로 남는다(실측): 비음악 영상으로 옮긴
 * 뒤에도 identifier·name·genre가 전부 이전 곡이었고 genre는 "Music"이었다. 반면
 * document.title·채널명은 750ms 안에 갱신됐다.
 *
 * 다행히 그 블록에는 **어느 영상의 것인지가 함께 실려 있다** — meta[itemprop=identifier]가
 * videoId다(실측: 이동 후 `identifier=s5Rkv_5Sbbo`인데 주소는 `v=jNQXAC9IVRw`). 그래서
 * "몇 ms 뒤면 갱신된다"는 시간 추측 대신 **값 자체로** 신선도를 판정한다.
 */
function microdataMatchesCurrentVideo(videoId: string): boolean {
  const id = document.querySelector<HTMLMetaElement>('meta[itemprop="identifier"]')?.content?.trim();
  if (id) return id === videoId;
  // identifier가 없는 레이아웃이면 워치 URL로 대조한다 (문서 순서상 첫 link[itemprop=url]가
  // 이 영상의 watch URL이다 — 뒤의 것들은 채널·썸네일 URL이다)
  const url = document.querySelector<HTMLLinkElement>('link[itemprop="url"]')?.getAttribute('href') ?? '';
  // 둘 다 없으면 판정할 근거가 없다 → 신선하다고 단정하지 않는다
  return url.includes(videoId);
}

/** 음악 영상 판별 — 유튜브 자체 신호 우선, 없으면 채널/제목 휴리스틱 */
function isLikelyMusicVideo(): boolean {
  // 1) 설명란 '음악' 섹션 (콘텐츠 ID로 곡이 식별된 영상) — 가장 신뢰
  if (document.querySelector('ytd-video-description-music-section-renderer')) return true;
  // 2) 워치 페이지 microdata 장르 — **이 영상의 microdata일 때만** 믿는다 (Music이 아니면 차단).
  //
  // 낡은 블록을 믿으면 양방향으로 틀린다: 음악 영상 뒤의 브이로그가 음악으로 판별돼 X로 닫은
  // 패널이 되살아나고(실측), 반대로 브이로그 뒤의 음악 영상은 차단된다. 낡았으면 아래 3·4단계
  // (채널·제목 — 750ms 안에 갱신되는 것이 실측으로 확인됐다)로 넘긴다.
  //
  // **대가를 알고 택한 것이다**: microdata는 SPA 이동에서 아예 갱신되지 않는다(두 번의 독립
  // 측정에서 12초까지 이전 영상 값). 즉 이 신호는 사실상 **전체 페이지 로드에서만** 쓸 수 있고,
  // SPA 이동에서는 제목·채널만 남는다. 그래서 제목에 표기가 없는 음악 영상은 SPA 이동 시
  // 자동으로 열리지 않는다(실측: 「하츠네 미쿠의 소실…【Official】」— 4단계 정규식이 【Official】을
  // 잡지 않는다). 그 반대(브이로그에서 패널이 되살아남)를 택하지 않은 이유는, 안 열리는 것은
  // 툴바 아이콘 한 번으로 사용자가 되돌릴 수 있는 반면 닫은 패널이 스스로 열리는 것은
  // 사용자가 되돌릴 방법이 없는 침입이기 때문이다. 자동 열기 범위를 되찾고 싶으면 4단계
  // 제목 정규식을 넓히는 것이 맞는 자리다(이 판정을 낡은 값으로 되돌리는 것이 아니다).
  const videoId = getCurrentVideoId();
  const genre = document.querySelector<HTMLMetaElement>('meta[itemprop="genre"]');
  if (genre?.content && videoId && microdataMatchesCurrentVideo(videoId)) {
    const g = genre.content.trim().toLowerCase();
    return g === 'music' || g === '음악';
  }
  // 3) 자동 생성 음악 채널(" - Topic")
  const channel = document.querySelector('ytd-watch-metadata ytd-channel-name a')?.textContent?.trim() ?? '';
  if (/ - Topic$/.test(channel)) return true;
  // 4) 제목 휴리스틱 — MV/가사/커버/보컬로이드 계열 표기.
  //
  // microdata가 SPA 이동에서 갱신되지 않으므로(2단계 주석) **이동 후에는 사실상 이 단계가
  // 유일한 양성 신호**다. 그래서 놓친 표기를 보태는 자리도 여기다.
  //
  // 이번에 추가한 것 (실측으로 놓친 것 + 음악에만 쓰이는 표기):
  //   · 괄호 안 Official·公式 — 실측 누락 「하츠네 미쿠의 소실(…)/cosMo＠폭주P【Official】」
  //   · Music Video — 기존 항목은 'Official' 뒤에만 붙어서 단독 표기를 못 잡았다
  //   · オリジナル曲 / ボカロ — 보카로 오리지널 곡 표기
  //
  // **일부러 넣지 않은 것** (나중에 넓힐 사람을 위한 근거):
  //   · 맨 Official·公式 (괄호 없이) — "Official Trailer"·"公式チャンネル"처럼 비음악에서
  //     더 흔하다. 브이로그·예고편에서 패널이 자동으로 열리는 것을 막으려고 방금 2단계를
  //     고쳤는데, 그 오탐을 제목 쪽으로 되들이는 셈이 된다. 그래서 **괄호 표기 안에 있을
  //     때만** 인정한다 — 【Official】·[Official]·(公式)는 음악 업로드의 태그 관습이다.
  //   · feat.·ft. — 원래부터 있던 항목이라 그대로 두었다(내가 넣은 것이 아니다).
  //   · 아티스트 구분자 '/'·'-' — 음악에 흔하지만 비음악 제목에도 흔해 오탐만 늘린다.
  //   · Lyric Video·MV — 이미 lyrics?·M\/?V가 잡는다(중복 추가 안 함).
  const title = document.title;
  return /(M\/?V|Official\s*(Music\s*)?Video|Music\s*Video|뮤직\s*비디오|가사|lyrics?|\bcover(ed)?\b|커버|불러보았다|歌ってみた|オリジナル曲|ボカロ|feat\.|ft\.|【[^】]*(MV|PV|오리지널|Original)[^】]*】|[【\[(（][^】\])）]*(?:Official|公式)[^】\])）]*[】\])）])/i.test(title);
}

/**
 * 추종을 시작할 때 본 document.title — 다음 이동에서 **DOM이 아직 이전 영상의 것인지**를
 * 값으로 판정하는 기준이다(시간으로 기다리지 않는다). 아래 checkCurrentPage 주석 참조.
 */
let followedPageTitle = '';
/** 판정을 미루고 있는 videoId와 미룬 횟수 — 같은 영상을 무한히 미루지 않기 위한 표식 */
let deferredVideoId: string | null = null;
let deferCount = 0;
/**
 * 추종 판정 보류 상한(연속 tick 수). tick은 이동 직후 1회 + 1.5초 간격이라 대략 6초다 —
 * 실측된 DOM 정체 최대 2초의 세 배쯤에서 끊는다.
 *
 * 상한을 둔 이유는 **제목이 영원히 같을 수 있기 때문**이다(제목이 완전히 같은 재업로드).
 * 그때 상한에 걸려 판정해도 손해가 없다: 그 제목은 **새 영상의 제목이기도** 하므로 판정이
 * 옳다. 반대로 상한이 없으면 그 영상은 영구히 무시된다.
 */
const MAX_FOLLOW_DEFERS = 4;

/** 이 영상을 추종하기 시작한다 — 판정 기준이 되는 제목도 이 시점 값으로 함께 새긴다 */
function beginFollowing(videoId: string): void {
  currentVideoId = videoId;
  followedPageTitle = document.title;
  deferredVideoId = null;
}

function checkCurrentPage(): void {
  const videoId = getCurrentVideoId();
  if (!videoId) {
    cleanupForPage();
    return;
  }
  if (videoId === currentVideoId) return;
  // SPA 이동 직후에는 **주소만 새 영상이고 DOM은 아직 이전 영상의 것**이다 — document.title과
  // 채널명이 이전 값으로 남는 것이 실측됐다(새 videoId + 이전 제목인 표본: 한 번은 750ms까지,
  // 다른 런에서는 2초까지). 그 창에서 판정하면 이전 곡의 음악성이 새 영상에 상속된다:
  // 비음악 영상(jNQXAC9IVRw)인데 이전 제목의 "(cover)"가 4단계에 걸려 추종이 시작됐고,
  // 패널은 한 번 열리면 스스로 닫히지 않아(searchLyrics는 setVisible(true)만 한다) 그 오판이
  // 그대로 굳었다 — 실측 22/24 표본.
  //
  // 시간으로 기다리지 않는다(창 길이가 런마다 750ms~2초로 들쭉날쭉했다). **값으로** 판정한다:
  // 제목이 아직 이전 영상의 것과 같으면 이번 tick은 넘기고 다음 tick(≤1.5초)에 다시 본다.
  // 보류 횟수는 MAX_FOLLOW_DEFERS로 묶는다(그 주석에 상한이 안전한 이유가 있다).
  //
  // 패널이 열려 있을 때는 미루지 않는다: shouldFollow가 DOM을 아예 보지 않고 true를 주므로
  // (첫 분기) 미룰 이유가 없고, 미루면 그 사이 cleanupForPage가 패널을 숨겨 곡을 넘길 때마다
  // 패널이 깜빡인다.
  const titleStale = followedPageTitle !== '' && document.title === followedPageTitle;
  if (!overlay?.isVisible() && titleStale) {
    if (deferredVideoId !== videoId) {
      deferredVideoId = videoId;
      deferCount = 0;
    }
    if (deferCount < MAX_FOLLOW_DEFERS) {
      deferCount++;
      cleanupForPage(); // 이전 곡 상태를 버리는 것은 지금 해도 안전하다 (판정만 미룬다)
      return;
    }
  }
  // 추종하지 않는 영상(브이로그·게임 등)이라도 **이전 곡 상태는 반드시 버린다.**
  // 유튜브는 <video> 엘리먼트를 재사용하므로 그냥 return하면 엔진이 새 영상의
  // currentTime을 읽는데 currentVideoId·currentData는 이전 곡에 고정된 채 남았다 —
  // PiP만 켜 둔 흐름에서 A의 가사가 B의 재생 위치에 맞춰 하이라이트되고, 켜 둔
  // 멜로디·메트로놈도 이전 곡 노트로 계속 울렸다. 정리는 cleanupForPage 하나로 한다
  // (PiP 창 자체는 닫지 않는다 — 그쪽 근거는 cleanupForPage 주석 참조).
  if (!shouldFollow()) {
    cleanupForPage();
    return;
  }
  beginFollowing(videoId);
  void searchLyrics();
}

/**
 * 이 페이지에서 볼 곡이 없어졌을 때의 정리 — 영상 없는 페이지(홈·검색)와 추종하지 않는
 * 영상으로의 이동이 같은 이 경로를 쓴다. 남기면 이전 곡이 새 영상 위에서 계속 도는 것이
 * 되므로(checkCurrentPage 주석) 곡에 매인 상태는 전부 버린다.
 */
function cleanupForPage(): void {
  if (currentVideoId === null) return;
  currentVideoId = null;
  currentSong = null;
  currentData = null;
  currentSourceUrl = null;
  videoOffset = 0;
  clearTimeout(offsetSaveTimer);
  // 알림은 영상별 사건이라 페이지를 떠나면 지운다 (검증 잡 추적 자체는 계속 살아 있고,
  // 그 영상으로 돌아오면 searchLyrics가 배지를 다시 세운다)
  showNotice(null);
  noticeVideoId = null;
  // 전사 잡은 서버에서 계속 돌므로 추적을 유지한다 (완료 시 해당 영상으로 돌아오면 반영)
  engine.stop();
  // PiP는 **사용자가 닫을 때만** 닫는다 — 다음 곡을 고르려 홈·검색을 거치는 것은 보통의
  // 사용법인데 여기서 pip.close()를 부르면 그때마다 창이 증발했다(pip.ts의 "창은 사용자가
  // 직접 닫기 전까지 살아 있다"는 설계와 정면으로 어긋난다). 대신 이전 곡의 내용을 비운
  // 빈 상태로 남겨, 그 창에서 바로 검색·붙여넣기를 계속할 수 있게 한다.
  if (pip.isOpen()) {
    pip.setLines([]); // 스테이지·레인에 남은 이전 곡 가사·노트 제거
    pip.setSong('', '');
    pip.setTempo(null);
    pip.setKey(null);
    pip.setDebugMeta(null);
    overlay?.setDebugMeta(null);
    pip.setGenerationChip(null);
    pip.showPanelEmpty(null);
    // 미러를 **반드시** 다시 붙인다 — 미러는 captureStream()이라 영상이 바뀌면 이전 트랙이
    // 끝나 프레임이 멈추고, PiP 영상 영역이 순수 검정(videoWidth=0)으로 남는다.
    // 예전에는 이 함수가 pip.close()를 불러 "미러가 죽은 빈 창"이 존재할 수 없었는데,
    // 창을 살려 두기로 하면서(위 근거) 이 경로에도 재부착이 필요해졌다. 유튜브는 SPA
    // 이동에서 <video> 엘리먼트를 재사용하므로 watchVideoBinding(엘리먼트 교체만 감지)은
    // 발동하지 않는다 — 실측: 이 경로로 들어가면 11초간 검정, applyLyricsData 경로(같은
    // 대상 영상, refreshPipMirror를 부른다)로 들어가면 videoW=320으로 정상.
    refreshPipMirror();
  }
  karaokeAudio.setNotes([]);
  karaokeAudio.setTempo(null);
  overlay?.setVisible(false);
}

async function toggleOverlay(): Promise<void> {
  if (overlay?.isVisible()) {
    overlay.setVisible(false);
    return;
  }
  const videoId = getCurrentVideoId();
  if (!videoId) return;
  ensureOverlay().setVisible(true);
  if (videoId !== currentVideoId || !currentData) {
    beginFollowing(videoId); // 수동으로 연 것도 추종 시작이다 — 판정 기준 제목을 함께 새긴다
    await searchLyrics();
  }
}

function ensureOverlay(): LyricsOverlay {
  if (overlay) return overlay;
  overlay = new LyricsOverlay(cssText, settings, {
    onSeek: time => engine.seekTo(time),
    onGenerate: text => void handleGenerate(text),
    onRetrySearch: query => void searchLyrics(query),
    onOffsetChange: offsetSec => {
      // 오프셋은 영상별 상태 — 서버에 저장해 다음 시청·다른 기기에서도 복원된다.
      // 링크로 빌려온 싱크(inst/커버)도 보는 영상 기준이라 영상마다 따로 저장된다.
      engine.setOffset(offsetSec);
      karaokeAudio.setOffset(offsetSec);
      videoOffset = offsetSec;
      scheduleOffsetSave();
    },
    onCloseSearch: () => {
      applyLyricsData(currentData);
      updateGenChip();
    },
    onSettingsChange: patch => void handleSettingsChange(patch),
    onRegenerate: () => void handleRegenerate(),
    onPipToggle: () => void handlePipToggle(),
    onGeometryChange: geometry => void saveGeometry(geometry),
    onCandidateSearch: query => void handleCandidateSearch(query),
    onPickCandidate: candidate => void handlePickCandidate(candidate),
    onLinkSync: (sourceVideoId, offsetSec, rate) => void handleLinkSync(sourceVideoId, offsetSec, rate),
    onCancelGenerate: () => void handleCancelGenerate(),
    onUnlinkSync: () => void handleUnlinkSync(),
    onRequestSyncList: () => void handleRequestSyncList(),
    onResetSync: () => void handleResetSync(),
    onRecheckServer: () => void refreshServerStatus(),
    onOpenPermissions: () => void openPermissionsPage(),
    loadServerLog: () => fetchServerLog(),
  }, initialGeometry);
  overlay.setServerStatus(serverStatus); // 이미 알고 있는 상태를 새 패널에 즉시 반영
  return overlay;
}

/** 최근 서버 요청 기록 — 백그라운드가 마스킹까지 마친 것을 그대로 받는다 */
async function fetchServerLog(): Promise<ServerLogEntry[]> {
  const res = await sendToBackground<ServerLogEntry[]>({ type: 'SERVER_LOG' });
  return res.data ?? [];
}

/** 오프셋 변경을 디바운스해 서버에 저장 (연타 중 매 클릭 요청 방지) */
function scheduleOffsetSave(): void {
  const videoId = currentVideoId;
  if (!videoId) return;
  clearTimeout(offsetSaveTimer);
  offsetSaveTimer = window.setTimeout(() => {
    void sendToBackground({ type: 'SYNC_OFFSET', payload: { videoId, offsetSec: videoOffset } });
  }, 800);
}

// ── 유튜브 자막 자동 폴백 ───────────────────────────────────────

/** 서버에서 자막 본문 받기 — timedtext는 브라우저에서 부르면 빈 본문이라(POT 강제, 실측) 서버 경유 */
async function fetchCaptionLines(
  videoId: string, lang: string, auto: boolean,
): Promise<CaptionLine[]> {
  const res = await sendToBackground<CaptionLine[]>({
    type: 'YT_CAPTION_TEXT', payload: { videoId, lang, auto },
  });
  return res.data ?? [];
}

/**
 * 서버 싱크·위키·LRCLIB이 전부 미스일 때 영상 자체 자막을 가사로 띄운다.
 *
 * 트랙 목록은 페이지에서 직접 읽어(서버 왕복 0) 자막 유무와 곡 언어를 즉시 판정하고,
 * 본문만 서버에서 받는다. 사용자가 트랙을 고르는 단계는 없다 — 못 고르겠으면 폴백을
 * 포기한다(번역 자막을 원문 가사인 양 띄우는 것보다 안 띄우는 편이 낫다).
 * 실패는 전부 null이라 호출부는 기존 "가사 없음" 화면으로 조용히 되돌아간다.
 */
async function tryCaptionFallback(
  videoId: string, song: SongInfo | null,
): Promise<LyricsData | null> {
  // 대사 위주 영상에서 자막이 가사인 양 뜨는 걸 막는다
  if (!isLikelyMusicVideo()) return null;

  const tracks = await getCaptionTracks(videoId);
  if (videoId !== currentVideoId) return null;
  const track = selectLyricTrack(tracks, song?.title ?? '');
  if (!track) return null;

  const base = await fetchCaptionLines(videoId, track.lang, track.auto);
  if (videoId !== currentVideoId || base.length === 0) return null;

  // 내 번역 언어의 수동 자막이 따로 있으면 시간 겹침으로 붙여 2단 표시 (수동작성만 —
  // 자동생성은 ASR 오차가 그대로 남아 가사 번역으로 쓰기엔 품질이 떨어진다). 예전엔
  // 한국어로 고정돼 있었다 — settings.translationLanguage 기준으로 일반화.
  let translations: (string | undefined)[] = [];
  const trTrack = selectTranslationTrack(tracks, track, settings.translationLanguage);
  if (trTrack) {
    const trLines = await fetchCaptionLines(videoId, trTrack.lang, trTrack.auto);
    if (videoId !== currentVideoId) return null;
    if (trLines.length > 0) translations = mergeCaptionTranslation(base, trLines);
  }
  // 실제로 한 줄이라도 자막에서 번역이 붙었으면 «사람 번역»으로 표시한다 — LLM이 덮어쓰지
  // 않게(hasMatchingHumanTranslation) + 이 세션에서 저장된 언어를 남긴다(translationLang).
  const humanTranslated = translations.some(t => t !== undefined);

  const lines: LyricLine[] = base.map((l, i) => ({
    time: l.start,
    endTime: l.end,
    text: l.text,
    translation: translations[i],
  }));
  return {
    source: 'caption',
    synced: true,
    lines,
    plainText: lines.map(l => l.text).join('\n'),
    // 자동 생성인지 남긴다 — 이 표시를 잃으면 ASR 전사가 싱크의 원문으로 승격된다
    captionAuto: track.auto,
    humanTranslated: humanTranslated || undefined,
    translationLang: humanTranslated ? settings.translationLanguage : undefined,
    // 출처 배지는 source가 'caption'이면 앞에 "유튜브 자막"을 이미 붙인다 —
    // 여기서 또 붙이면 "유튜브 자막 · 유튜브 자막 · 일본어…"로 겹친다
    attribution: { name: captionSourceLabel(track) },
  };
}

// ── 싱크 링크 (inst·커버 영상이 다른 영상의 전사를 재사용) ──────────

async function handleLinkSync(sourceVideoId: string, offsetSec: number, rate: number): Promise<void> {
  const videoId = currentVideoId;
  if (!videoId) return;
  if (sourceVideoId === videoId) {
    ensureOverlay().setLinkStatus(t('content.link.cannotSelf'));
    return;
  }
  // 자체 전사가 있으면 조회가 링크보다 자체 전사를 우선해 연결이 무시된다 —
  // 사용자가 명시적으로 연결을 원했으니 확인 후 자체 전사를 지우고 연결한다
  if (currentData?.synced && currentData.source === 'everyric' && !currentData.linked) {
    const ok = window.confirm(t('content.link.replaceOwnConfirm'));
    if (!ok) {
      ensureOverlay().setLinkStatus(t('content.link.cancelledKeepOwn'));
      return;
    }
    const reset = await sendToBackground<{ removed_syncs: number }>({
      type: 'SYNC_RESET', payload: { videoId },
    });
    if (reset.error) {
      const note = failureNote(noteFailure(reset.failure));
      ensureOverlay().setLinkStatus(t('content.link.ownDeleteFailed', [note ? ` — ${note}` : '']));
      return;
    }
    for (const key of [...translationCache.keys()]) {
      if (key.startsWith(`${videoId}:`)) translationCache.delete(key);
    }
  }
  const res = await sendToBackground<Record<string, unknown>>({
    type: 'SYNC_LINK',
    payload: { videoId, sourceVideoId, offsetSec, rate },
  });
  if (videoId !== currentVideoId) return;
  if (res.error || !res.data) {
    // 원본에 전사가 없어서일 수도, 서버 자체 문제일 수도 있다 — 아는 사유가 있으면 그것을 쓴다
    const note = failureNote(noteFailure(res.failure));
    ensureOverlay().setLinkStatus(note
      ? t('content.link.failedWithNote', [note])
      : t('content.link.failedNoNote'));
    return;
  }
  void searchLyrics(); // 링크된 싱크를 즉시 불러온다
}

async function handleUnlinkSync(): Promise<void> {
  const videoId = currentVideoId;
  if (!videoId) return;
  const res = await sendToBackground<{ removed: boolean }>({ type: 'SYNC_UNLINK', payload: { videoId } });
  if (videoId !== currentVideoId) return;
  if (res.error) {
    const note = failureNote(noteFailure(res.failure));
    ensureOverlay().setLinkStatus(t('content.link.unlinkFailed', [note ? ` — ${note}` : t('content.link.unlinkFailedCheckServer')]));
    return;
  }
  ensureOverlay().setLinked(null);
  void searchLyrics();
}

// ── 커버 자동 연결 (같은 곡의 다른 영상 싱크를 서버가 찾아 붙인다) ──────
//
// 사용자 증상이었던 것: 원곡을 싱크한 뒤 같은 곡의 다른 영상을 처음 열면 매번 손으로
// 연결해야 했다. 서버에는 이미 후보 탐색 + 반주 상관 검증 + 자동 링크 생성이 다 있고,
// 빠져 있던 것은 **클라이언트가 그 경로를 부르지 않는다**는 것뿐이었다.
//
// 여기서 하는 일은 세 가지다:
//   1) 싱크가 없을 때만 후보를 물어본다 (있는 다수 케이스에 지연을 주지 않는다).
//   2) 검증 잡이 제출되면 기다리지 않고 배지만 띄우고 백그라운드로 넘긴다.
//   3) 폴링해서 링크가 생기면 다시 조회해 가사를 띄우고, 아니면 조용히 원래대로 둔다.
// 판정(같은 곡인가)은 전부 서버 몫이다 — 제목이 맞았다는 이유로 링크가 생기지 않는다.

/**
 * 같은 곡의 다른 영상 후보 탐색 — **서버 싱크가 없는 영상에서만** 부른다.
 *
 * 실패는 모두 조용하다: 엔드포인트가 없는 구버전 서버(404)·오프라인·오류 어느 경우든
 * 아무 표시 없이 기존 "가사 없음" 상태 그대로 남는다. 이 기능은 없으면 없는 대로
 * 동작해야 하고, 사용자가 요청하지도 않은 배경 작업의 실패로 화면을 어지럽히면 안 된다.
 * (그래서 noteFailure로 전역 서버 상태를 건드리지도 않는다 — 바로 앞의 싱크 조회가
 * 이미 같은 서버를 찔러 상태를 갱신했다.)
 */
async function probeLinkCandidates(videoId: string, song: SongInfo): Promise<void> {
  if (!song.title.trim()) return; // 제목 없이는 서버가 후보를 찾을 수 없다 (422)
  if (linkProbed.has(videoId) || linkJobs.has(videoId)) return;
  // 서버가 고장난 걸 이미 아는 상태면 찔러 볼 이유가 없다 (오류 배너가 이미 떠 있다)
  if (serverKnownBad(serverStatus)) return;
  linkProbed.add(videoId);

  const res = await sendToBackground<LinkCandidatesResponse>({
    type: 'LINK_CANDIDATES',
    payload: { videoId, title: song.title, artist: song.artist ?? undefined },
  });
  const data = res.data;
  if (!data) return;

  // submitted·pending만 사용자에게 알린다. cooldown(최근에 이미 시도)·none(후보 없음)·
  // disabled(서버 설정 off)·has_sync·linked는 사용자가 할 수 있는 일이 없어 소음이다.
  if ((data.status === 'submitted' || data.status === 'pending') && data.job_id) {
    linkJobs.set(videoId, { linkJobId: data.job_id, title: song.title, started: Date.now() });
    if (videoId === currentVideoId) showNotice(t('content.linkProbe.chip'));
    ensurePolling();
  }
}

/**
 * 진행 중인 검증 잡 폴링 — 전사 잡과 같은 타이머(pollJobs)에 얹혀 돈다.
 *
 * done+match면 서버가 이미 SyncLink를 만들었으므로 재조회만 하면 가사가 내려온다.
 * 미매치·실패는 **조용히** 추적만 정리한다 (기대하지 않았던 것이 안 됐을 뿐이다).
 */
async function pollLinkJobs(): Promise<void> {
  for (const [videoId, job] of [...linkJobs]) {
    // 워커가 없어 큐에 머무는 잡을 영원히 찔러 보지 않는다 — 조용히 지켜보기를 그만둔다
    if (Date.now() - job.started > LINK_JOB_WATCH_MS) {
      linkJobs.delete(videoId);
      if (videoId === currentVideoId) showNotice(null);
      continue;
    }
    const res = await sendToBackground<LinkJobStatusResponse>({
      type: 'LINK_JOB_STATUS', payload: { linkJobId: job.linkJobId },
    });
    if (linkJobs.get(videoId)?.linkJobId !== job.linkJobId) continue; // 그 사이 정리됨
    const status = res.data;
    if (!status) continue; // 일시적 실패 — 다음 폴링에서 재시도
    if (status.status !== 'done' && status.status !== 'failed') continue; // queued·processing

    linkJobs.delete(videoId);
    const linked = status.status === 'done' && status.match === true;
    if (videoId !== currentVideoId) continue; // 다른 영상 결과는 알리지 않는다 (돌아오면 조회에 반영된다)
    if (!linked) {
      showNotice(null); // 미매치·실패는 조용히 원래 상태로
      continue;
    }
    const conf = status.confidence != null ? t('content.link.autoConfSuffix', [String(Math.round(status.confidence * 100))]) : '';
    showNotice(t('content.link.autoLinked', [conf]), 12000);
    void searchLyrics(); // 링크된 싱크를 즉시 불러온다
  }
}

async function handleRequestSyncList(): Promise<void> {
  const res = await sendToBackground<SyncListItem[]>({ type: 'SYNC_LIST' });
  noteFailure(res.failure); // 빈 목록이 "없음"인지 "못 받음"인지 상태로 남긴다
  overlay?.showSyncList(res.data ?? []);
}

async function handleSettingsChange(patch: Partial<Settings>): Promise<void> {
  settings = await saveSettings(patch);
  if (patch.uiLanguage !== undefined) setUiLanguage(settings.uiLanguage);
  overlay?.applySettings(settings);
  // 키를 고치는 것이 인증 실패의 정상 복구 경로다 — URL과 함께 즉시 재확인한다
  if (patch.serverUrl !== undefined || patch.apiKey !== undefined) void refreshServerStatus();
  if (patch.debugInfo !== undefined) pip.setDebug(patch.debugInfo);
  // 메인 패널은 위 applySettings에서 이미 바뀐다 — PiP도 같은 판정값으로 함께 맞춘다
  if (patch.theme !== undefined) pip.setTheme(resolveTheme(settings));

  if (patch.translationLanguage && settings.showTranslation) {
    // 언어를 바꿨으면 **이미 실린 번역을 먼저 비운다.** loadTranslations의 조기 반환은
    // "모든 줄에 번역이 있으면 끝"이라 어느 언어의 번역인지 보지 않는다 — 비우지 않으면
    // ko→en으로 바꿔도 재요청이 일어나지 않고 한국어 번역이 그대로 남는다(브라우저 검증에서
    // 상태 문구가 아예 뜨지 않는 것으로 관측됐다). 캐시 키에는 언어가 들어 있으니 조회
    // 자체는 옳다 — 잘못된 것은 조회에 닿기 전에 반환하는 이 가드뿐이다.
    clearTranslations();
    void loadTranslations();
  } else if (patch.showTranslation === true) {
    void loadTranslations();
  } else if (patch.showTranslation === false) {
    clearTranslations();
  }

  // PiP 사용 중 패널 유지 설정을 토글하면 즉시 반영
  if (patch.pipKeepPanel !== undefined && pip.isOpen() && currentData?.synced) {
    if (patch.pipKeepPanel) {
      applyLyricsData(currentData);
    } else {
      overlay?.showPipPlaceholder();
    }
  }

  // PiP 영상 표시 토글 즉시 반영
  if (patch.pipShowVideo !== undefined && pip.isOpen()) {
    pip.setVideoEnabled(patch.pipShowVideo, engine.getVideo() ?? getVideoElement());
  }

  // 발음 표기 토글 즉시 반영 (패널은 applySettings에서 처리됨)
  if (patch.showPronunciation !== undefined) {
    pip.setShowPronunciation(patch.showPronunciation);
  }

  // 발음 표기 방식(hangul/romaji/kana) 즉시 반영 — pronunciationScript 자체를 바꿨을 때는
  // 물론, 'auto'일 때는 translationLanguage가 바뀌어도 해석 결과가 달라지므로 함께 본다.
  // 메인 패널의 이미 그려진 줄은 다음 showSyncedLyrics 호출(곡 전환 등)에서 새 표기를 반영한다
  // — 지금은 서버가 표기별 발음(pron dict)을 아직 안 주므로 화면상 차이는 없다.
  if (patch.pronunciationScript !== undefined || patch.translationLanguage !== undefined) {
    pip.setPronScript(resolveScript(settings));
  }

  // 디버그 토글 → 레인 신뢰도 색상도 함께
  if (patch.debugInfo !== undefined) {
    pip.setShowConfidence(patch.debugInfo);
  }

  // 레인 표시 구간/진행 방식/글자 크기/카운트다운 즉시 반영
  if (patch.pitchWindowMeasures !== undefined) {
    pip.setPitchWindow(patch.pitchWindowMeasures);
  }
  if (patch.pitchScrollMode !== undefined) {
    pip.setPitchScrollMode(patch.pitchScrollMode);
  }
  if (patch.pitchFontScale !== undefined) {
    pip.setPitchFontScale(patch.pitchFontScale);
  }
  if (patch.pitchCountdown !== undefined) {
    pip.setPitchCountdown(patch.pitchCountdown);
  }
  if (patch.pitchPronPosition !== undefined) {
    pip.setPitchPronPosition(patch.pitchPronPosition);
  }

  // 가라오케 음정 바 토글 즉시 반영
  if (patch.pitchGuide !== undefined) {
    pip.setPitchEnabled(patch.pitchGuide);
  }

  // 멜로디/메트로놈/마이크 — 토글·볼륨·배속·시작박·기기 변경 즉시 반영
  if (
    patch.melodyPlayback !== undefined || patch.melodyVolume !== undefined ||
    patch.metronome !== undefined || patch.metronomeVolume !== undefined ||
    patch.metronomeRate !== undefined || patch.metronomeBeat !== undefined ||
    patch.audioOutputId !== undefined || patch.micPitch !== undefined ||
    patch.micDeviceId !== undefined
  ) {
    applyAudioSettings();
  }
  if (patch.metronomeRate !== undefined || patch.metronomeBeat !== undefined) {
    pip.setMetronomeConfig(settings.metronomeRate, settings.metronomeBeat);
  }
  if (patch.micOctave !== undefined) {
    pip.setMicOctave(settings.micOctave);
  }
  if (patch.pitchF0Curve !== undefined) {
    pip.setShowF0(settings.pitchF0Curve);
  }

  // 저신뢰 경고 토글 즉시 반영
  if (patch.lowConfWarning !== undefined) {
    overlay?.setQualityWarning(
      settings.lowConfWarning && currentData?.synced && currentData.source === 'everyric'
        && currentData.qualityScore != null && currentData.qualityScore < 0.001
        ? currentData.qualityScore
        : null,
    );
  }

  if (patch.debugInfo === true) pushDebug(null);
}

/** 멜로디/메트로놈/마이크 상태를 설정에 맞춰 동기화 — 가라오케 창(PiP)이 열려 있을 때만 소리·검출 */
function applyAudioSettings(): void {
  karaokeAudio.configure({
    melody: settings.melodyPlayback,
    melodyVolume: settings.melodyVolume,
    metronome: settings.metronome,
    metronomeVolume: settings.metronomeVolume,
    metronomeRate: settings.metronomeRate,
    metronomeBeat: settings.metronomeBeat,
    sinkId: settings.audioOutputId,
  });
  pip.setAudioState(settings.melodyPlayback, settings.metronome);
  if (pip.isOpen() && settings.micPitch) {
    if (micPitch.isRunning() && micPitch.currentDeviceId() !== settings.micDeviceId) micPitch.stop();
    if (!micPitch.isRunning()) void micPitch.start(settings.micDeviceId || undefined);
  } else if (micPitch.isRunning()) {
    micPitch.stop();
  }
}

/** 현재 시각이 어떤 구간으로 판정됐는지 (star 흡수/가창/간주) — everyric 소스만 */
function debugZoneAt(time: number | null): string | null {
  const meta = currentData?.debugMeta;
  if (!meta || time === null) return null;
  const relT = time - videoOffset;
  if (meta.star_spans?.some(([s, e]) => relT >= s && relT < e)) return t('content.debug.zoneAdlib');
  if (meta.vad_regions == null) return null;
  return meta.vad_regions.some(([s, e]) => relT >= s && relT < e) ? t('content.debug.zoneVocal') : t('content.debug.zoneInstrumental');
}

/** 디버그 스트립에 현재 내부 상태를 밀어넣는다 (설정 꺼져 있으면 no-op) */
function pushDebug(time: number | null): void {
  if (!settings.debugInfo || !overlay) return;
  const bound = engine.getVideo();
  const dom = getVideoElement();
  const video = bound ?? dom;
  const line = lastLineIndex >= 0 ? currentData?.lines[lastLineIndex] : undefined;
  const lineDebug = line?.debug
    ? `act=${Math.round((line.debug.activeRatio ?? 0) * 100)}%${line.debug.clamped ? ' CLAMP' : ''}`
    : null;
  overlay.updateDebug({
    zone: debugZoneAt(time ?? (video ? video.currentTime : null)),
    lineDebug,
    videoId: currentVideoId,
    source: currentData?.source ?? '-',
    synced: currentData?.synced ?? false,
    time: time ?? (video ? video.currentTime : null),
    offsetSec: videoOffset,
    lineIndex: lastLineIndex,
    lineCount: currentData?.lines.length ?? 0,
    videoBound: bound !== null && (dom === null || bound === dom),
    videoInfo: video ? `rs${video.readyState},${video.paused ? 'pause' : 'play'}` : 'none',
    engineRunning: engine.isRunning(),
    pipOpen: pip.isOpen(),
    jobStatus: currentJobStatus(),
    quality: currentData?.qualityScore ?? null,
    ...lineConfSummary(),
    alignmentText: currentData?.debugMeta?.alignment_text ?? null,
    syncCreated: formatSyncCreated(currentData?.createdAt),
  });
}

/**
 * 서버 싱크 생성 시각 → «2026-07-25 18:59 (3시간 전)».
 *
 * 파이프라인을 고쳐도 **이미 만들어진 싱크는 그대로**다. 지금 보고 있는 싱크가 언제 만들어진
 * 것인지 모르면 "고쳐진 건지 옛 결과를 보고 있는 건지"를 판단할 수 없어서, 경과 시간까지 함께
 * 보여 준다.
 *
 * 서버 문자열("2026-07-25 09:59:51")에는 타임존 표기가 없지만 값은 UTC다 — 그대로 Date에
 * 넘기면 브라우저가 현지 시각으로 읽어 9시간(KST) 밀린다. 표기가 없을 때만 Z를 붙여 못 박는다.
 */
function formatSyncCreated(raw: string | undefined): string | null {
  if (!raw) return null;
  const text = raw.trim();
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(text);
  const at = new Date(hasZone ? text : `${text.replace(' ', 'T')}Z`);
  if (Number.isNaN(at.getTime())) return text; // 형식이 바뀌면 원본을 그대로 보여 준다
  const pad = (n: number) => String(n).padStart(2, '0');
  const stamp = `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}`
    + ` ${pad(at.getHours())}:${pad(at.getMinutes())}`;
  const mins = Math.round((Date.now() - at.getTime()) / 60000);
  const age = mins < 1 ? t('content.age.justNow')
    : mins < 60 ? t('content.age.minutesAgo', [String(mins)])
      : mins < 60 * 48 ? t('content.age.hoursAgo', [String(Math.round(mins / 60))])
        : t('content.age.daysAgo', [String(Math.round(mins / 1440))]);
  return `${stamp} (${age})`;
}

function currentJobStatus(): string | null {
  const cur = currentVideoId ? generatingJobs.get(currentVideoId) : undefined;
  if (cur) return `job=${cur.jobId.slice(0, 8)}(${cur.progress}%)`;
  return generatingJobs.size > 0 ? `bg-jobs=${generatingJobs.size}` : null;
}

/** 라인 confidence의 median·등급 분포 — 곡 전체 정렬 품질 요약 (디버그 표시용) */
function lineConfSummary(): {
  qualityMed: number | null;
  lowConfRatio: number | null;
  confGrades: { ok: number; mid: number; low: number } | null;
} {
  const vals = (currentData?.lines ?? [])
    .map(l => l.confidence)
    .filter((v): v is number => v != null)
    .sort((a, b) => a - b);
  if (vals.length === 0) return { qualityMed: null, lowConfRatio: null, confGrades: null };
  const low = vals.filter(v => v < 1e-4).length / vals.length;
  const mid = vals.filter(v => v >= 1e-4 && v < 2e-2).length / vals.length;
  return {
    qualityMed: vals[Math.floor(vals.length / 2)],
    lowConfRatio: low,
    confGrades: { ok: 1 - low - mid, mid, low },
  };
}

/**
 * 서버 싱크(everyric) 라인에 보카로 위키의 발음/사람 번역을 텍스트 매칭으로 입힌다.
 * 싱크가 위키 가사로 생성됐다면 라인 텍스트가 그대로 보존되므로 대부분 1:1로 매칭된다.
 */
async function enrichFromVocaro(videoId: string, data: LyricsData): Promise<void> {
  let lines: VocaroLine[] | null = lastVocaro?.videoId === videoId ? lastVocaro.lines : null;
  if (!lines) {
    let slug: string | null = null;
    try {
      const stored = await chrome.storage.local.get(`vocaroRef:${videoId}`);
      // 구버전은 슬러그 문자열만 저장했다 — 새 형식({slug, t})과 둘 다 읽는다
      const raw = stored[`vocaroRef:${videoId}`] as string | { slug?: string } | undefined;
      slug = typeof raw === 'string' ? raw : raw?.slug ?? null;
    } catch { /* storage 실패 → 병합 생략 */ }
    if (!slug) return;
    const res = await sendToBackground<VocaroResult | null>({ type: 'VOCARO_PAGE', payload: { slug } });
    lines = res.data?.lines ?? null;
    if (lines) lastVocaro = { videoId, lines };
  }
  if (!lines) return;

  const norm = (s: string) => s.replace(/\s+/g, ' ').trim();
  const byText = new Map<string, VocaroLine>();
  for (const l of lines) {
    if (l.text && !byText.has(norm(l.text))) byText.set(norm(l.text), l);
  }
  for (const line of data.lines) {
    const v = byText.get(norm(line.text));
    if (!v) continue;
    // 발음(한글 표기)은 어떤 번역 언어에서도 유효하다 — 표시 시점에 resolvedPronunciation이
    // script==='hangul'일 때만 노출하므로 여기서 언어를 가릴 필요가 없다.
    if (v.pronunciation && !line.pronunciation) line.pronunciation = v.pronunciation;
    // 번역은 vocaro가 **한국어 전용**이다 — 언어 가드 없이 병합하면 en/ja 타깃 사용자에게도
    // 이 곡을 예전에(또는 다른 사용자가) 한국어로 본 세션의 vocaroRef가 남아 있을 때마다
    // 한국어 번역이 새어나간다(코드 감사로 발견, 실사용 미확인 — vocaroRef는 storage에
    // videoId 단위로 영구 보관되고 언어별로 구분되지 않는다). 내 번역 언어가 한국어일
    // 때만 싣고, translationLang을 함께 남겨 hasMatchingHumanTranslation이 정확히 판정한다.
    if (v.translation && !line.translation && settings.translationLanguage === 'ko') {
      line.translation = v.translation;
      data.humanTranslated = true;
      data.translationLang = settings.translationLanguage;
    }
  }
}

/**
 * 이 데이터의 사람 번역이 지금 보는 번역 언어와 같은 언어인가 — 같으면 지우지도, LLM으로
 * 다시 받지도 않는다(clearTranslations·loadTranslations가 공유하는 가드).
 *
 * vocaro는 한국어 전용, miraheze는 영어 전용이다 — attribution.sourceId로 실제 출처를
 * 가른다(구데이터 vocaro 채택분은 attribution이 없으므로 source==='vocaro' 폴백을
 * 유지한다 — adoptVocaroResult는 손대지 않았다). 이 둘은 단일 언어 소스라 언어 자체가
 * 고정이지만, 그 밖의 humanTranslated(서버 sync의 wiki 병합분·유튜브 수동 자막 등)는
 * **임의 언어**일 수 있으므로 data.translationLang(그 번역이 실제로 실린 언어)이 내
 * 번역 언어와 같을 때만 보호한다 — 예전엔 여기도 한국어로 고정돼 있어서 en/ja 타깃에
 * 실제로 자막 병합이 있어도 매번 지우고 LLM을 다시 불렀다.
 */
function hasMatchingHumanTranslation(data: LyricsData): boolean {
  if (data.attribution?.sourceId === 'miraheze') return settings.translationLanguage === 'en';
  if (data.source === 'vocaro') return settings.translationLanguage === 'ko';
  return Boolean(data.humanTranslated) && data.translationLang === settings.translationLanguage;
}

function clearTranslations(): void {
  overlay?.setTranslationStatus(null);
  if (!currentData) return;
  // 사람 번역(위키 등)은 가사 자체의 일부 — 지우지 않는다(내 번역 언어와 같은 위키일 때만).
  if (hasMatchingHumanTranslation(currentData)) return;
  for (const line of currentData.lines) delete line.translation;
  overlay?.refreshTranslations();
  pip.refresh();
}

/**
 * 이미 서버 싱크가 있는 곡(everyric·synced)에서 내 번역 언어의 유튜브 수동 자막을
 * LLM보다 먼저 시도한다 — tryCaptionFallback(첫 로딩·싱크 자체가 없을 때의 폴백)과 같은
 * 원칙을 "이미 싱크가 있어 번역 레이어만 미스인" 사후 경로에 적용한 것.
 *
 * **저장(persist)은 하지 않는다** — 이 세션(currentData) 동안만 유지되고 새로고침하면
 * 다시 이 경로를 탄다. everyric-api에 "이미 있는 텍스트를 그대로 레이어에 저장"할 경로가
 * 없기 때문이다: translateLyrics(/api/translate persist=true)는 항상 LLM이 **직접**
 * 번역해 그 결과를 저장한다 — 자막 원문을 text로 실어 보내도 서버가 그것을 재번역해 버려
 * 자막을 쓰는 의미(사람이 쓴 표현 그대로)가 사라진다. attachLineMeta는
 * `/api/sync/jobs/{jobId}/line-meta`로 생성 잡에 매달린 엔드포인트라, 잡이 없는 순수
 * 조회 세션(여기)에는 jobId 자체가 없어 호출할 수 없다. 즉 오늘 기준으로는 "표시는 되지만
 * 서버에는 안 남는" 상태가 맞는 결론이다 — 저장하려면 서버에 새 엔드포인트(원문 그대로
 * 받는 레이어 저장)가 필요하고, 이번 작업은 서버 변경이 금지돼 있다.
 */
async function tryCaptionTranslationLayer(
  data: LyricsData, videoId: string, lang: string,
): Promise<boolean> {
  if (data.source !== 'everyric' || !data.synced) return false;
  const base: CaptionLine[] = data.lines
    .filter((l): l is LyricLine & { time: number; endTime: number } => l.time != null && l.endTime != null)
    .map(l => ({ start: l.time, end: l.endTime, text: l.text }));
  // 타이밍 없는 줄이 하나라도 섞여 있으면 base와 data.lines의 인덱스가 어긋난다 — 포기.
  if (base.length !== data.lines.length) return false;
  const tracks = await getCaptionTracks(videoId);
  if (videoId !== currentVideoId) return false;
  // chosen(원어 트랙)을 모른다 — 대각선은 loadTranslations의 조기 반환이 이미 걸러
  // 여기 닿을 때는 곡 스크립트!=lang이 보장되므로 null로 넘겨도 안전하다.
  const track = selectTranslationTrack(tracks, null, lang);
  if (!track) return false;
  const trLines = await fetchCaptionLines(videoId, track.lang, track.auto);
  if (videoId !== currentVideoId) return false;
  if (trLines.length === 0) return false;
  const merged = mergeCaptionTranslation(base, trLines);
  if (!merged.some(m => m !== undefined)) return false;
  // applyTranslations를 재사용 — translationLang 기록·availableLangs 칩 반영·pip 갱신까지
  // 기존 LLM 경로와 동일하게 맞아떨어진다. pronunciation은 안 실어 보낸다(자막엔 없다).
  applyTranslations(data, data.lines.map((line, i) => ({ original: line.text, translation: merged[i] ?? '' })));
  data.humanTranslated = true;
  return true;
}

async function loadTranslations(): Promise<void> {
  const data = currentData;
  const videoId = currentVideoId;
  if (!data || !videoId || !settings.showTranslation) return;
  const srcLines = data.lines.map(l => l.text);
  const lang = settings.translationLanguage;
  // 대각선(J3) — 곡 원문 스크립트가 내 번역 언어와 같으면 서버는 번역을 만들지 않는다
  // (expectsPronunciation의 `script === lang` 분기와 같은 사실을 여기서도 재사용할 뿐,
  // 그 매트릭스 자체는 건드리지 않는다). 이전에는 이 사실을 모른 채 매 로딩마다 요청을
  // 쏘고 "번역 생성 중…" 문구가 잠깐 떴다가 서버의 조용한 거절로 사라졌다 — 헛호출+깜빡임.
  // 사용자가 칩·설정으로 방금 이 언어로 전환한 직후에도(handleSettingsChange → clearTranslations
  // → loadTranslations 경로) 이 가드가 그대로 적용돼 같은 무요청·무깜빡임이 보장된다.
  if (detectSongScript(srcLines) === lang) return;
  // 위키 사람 번역(내 번역 언어와 같을 때만)이 있으면 발동하지 않는다
  // (clearTranslations의 같은 가드와 규칙을 맞춘다)
  if (hasMatchingHumanTranslation(data)) return;
  // 서버 싱크에 번역·발음이 이미 저장돼 있으면(생성 시 LLM 메타 병합) LLM 재호출 생략.
  // 단, 발음이 기대되는 원문(일본어 등 CJK)인데 발음이 하나도 없으면 — 번역만 저장된
  // 낡은 싱크 — 발음까지 다시 받아온다 (그냥 반환하면 발음이 영영 채워지지 않는다)
  //
  // 다국어 가드 — data.translationLang(서버 EveryricSyncResponse.translation_lang을 그대로
  // 옮긴 값, 또는 이 세션에서 applyTranslations가 새 번역을 적용한 뒤 직접 채운 값)이
  // 있으면 그 언어가 내 설정과 같을 때만 "이미 있음"으로 본다. 값이 없으면(구서버 응답이거나
  // background가 아직 lang 쿼리를 서버에 넘기지 않는 배선 갭) 예전 규칙(모든 줄에 번역이
  // 있으면 충분)으로 폴백한다 — 그 경우 한국어권 밖 사용자는 여전히 남의 언어 번역을 보고
  // "이미 있다"고 오판될 수 있다. 확장의 로컬 캐시(translationKey)는 언어를 이미 구분하므로
  // 캐시 경로는 이 문제가 없다.
  const expectsPron = expectsPronunciation(srcLines);
  // translationLang이 있으면(서버가 채워줬거나 이 세션에서 applyTranslations가 직접 채운
  // 값) 그 언어가 내 언어와 같을 때만 "이미 있음"으로 본다. 필드가 없으면(구서버·아직
  // 안 채워짐) 예전 규칙 그대로 — 모든 줄에 translation이 있으면 충분하다고 본다.
  const translationLangMatches = data.translationLang == null || data.translationLang === lang;
  if (
    translationLangMatches
    && data.lines.every(l => l.translation)
    && (data.lines.some(l => l.pronunciation) || !expectsPron)
  ) return;
  // 지금 화면의 원문 지문으로 조회한다 — 소스를 갈아탄 뒤 남아 있던 다른 원문의 번역이
  // 위치로 얹히는 것을 키 단계에서 막는다 (translationKey 주석의 실제 경로)
  const cached = translationCacheGet(translationKey(videoId, lang, srcLines));
  // 캐시도 같은 기준으로 검증 — 발음 빠진 캐시(구버전 응답)는 다시 받아온다
  if (cached && (!expectsPron || cached.some(l => l.pronunciation))) {
    applyTranslations(data, cached);
    return;
  }

  // 서버 번역 레이어가 미스라도(이 아래로 내려왔다는 것 자체가 미스) 내 언어의 유튜브
  // 수동 자막이 있으면 LLM보다 먼저 그것을 쓴다 — 사람이 쓴 자막이 LLM보다 낫다는 원칙은
  // tryCaptionFallback(첫 로딩 폴백)과 같다. 대각선(곡 스크립트==내 언어)은 이 지점에
  // 닿기 전에 이미 위에서 반환됐으므로 여기서 따로 확인하지 않는다.
  if (await tryCaptionTranslationLayer(data, videoId, lang)) return;
  if (currentData !== data || currentVideoId !== videoId) return; // 곡이 바뀜(자막 시도 중)

  // 번역은 서버 전용이다 — 고장난 걸 알면서 "생성 중…"을 띄우는 건 작동하는 척하는 것
  if (serverKnownBad(serverStatus)) {
    overlay?.setTranslationStatus(t('content.translation.unavailable', [statusLine(serverStatus)]));
    return;
  }
  overlay?.setTranslationStatus(t('content.translation.generating'));
  // 제목바 언어 칩 로딩 표시 — 응답이 오면(성공이든 실패든) 바로 아래서 끈다.
  // 곡이 바뀌면 applyLyricsData가 이미 null로 되돌려 놨을 수 있어 순서가 안 꼬인다.
  overlay?.setLangPending(lang);
  const lines = await requestTranslation(videoId, srcLines);
  overlay?.setLangPending(null);
  if (currentData !== data || currentVideoId !== videoId) return; // 곡이 바뀜
  if (!settings.showTranslation || settings.translationLanguage !== lang) return;

  if (!lines || lines.length === 0) {
    // requestTranslation이 실패 사유를 이미 상태에 반영했다 — 그 사유를 그대로 보여 준다
    overlay?.setTranslationStatus(serverKnownBad(serverStatus)
      ? t('content.translation.failedWithStatus', [statusLine(serverStatus)])
      : t('content.translation.failedNoResult'));
    return;
  }
  applyTranslations(data, lines);
}

function applyTranslations(data: LyricsData, translated: TranslatedLine[]): void {
  // 적용은 **인덱스 위치**로만 이뤄진다 — 줄 수가 다르면 전부 어긋난 줄에 붙는다.
  // 생성 경로(fetchLlmLineMeta)에는 이 대조가 있었는데 표시 경로에는 없어서, 두 경로가
  // 서로 다른 규칙으로 동작하는 것 자체가 결함이었다. 키의 원문 지문이 1차로 막지만,
  // 서버 응답이 줄을 합치거나 빠뜨리는 경우는 지문으로 걸러지지 않으므로 여기서 확인한다.
  // 어긋난 번역을 붙이는 것보다 안 붙이고 사유를 말하는 편이 낫다.
  if (translated.length !== data.lines.length) {
    overlay?.setTranslationStatus(
      t('content.translation.lineCountMismatch', [String(translated.length), String(data.lines.length)]),
    );
    return;
  }
  // 이 배치가 어느 언어인지 세션 상태에 남긴다 — 서버가 translation_lang을 안 주는 구버전
  // 이거나 background가 lang 쿼리를 아직 안 넘기는 배선 갭이 있어도, 최소한 "방금 이
  // 언어로 번역을 적용했다"는 사실만은 클라이언트가 스스로 안다(loadTranslations의
  // translationLangMatches 가드가 이 값을 읽는다)
  data.translationLang = settings.translationLanguage;
  // 제목바 언어 칩 — 이 언어의 번역이 방금 실제로 생겼으니 서버 재조회 없이 "보유"로
  // 바로 반영한다(everyric 소스에만 의미가 있다 — availableLangs는 그 서버 레이어 개념).
  if (data.source === 'everyric') {
    data.availableLangs = data.availableLangs?.includes(data.translationLang)
      ? data.availableLangs
      : [...(data.availableLangs ?? []), data.translationLang];
    // availableLangsForChip을 거쳐야 곡 자신의 언어 칩이 계속 "보유" 스타일을 유지한다 —
    // 여기서 data.availableLangs를 그대로 넘기면 방금 병합한 목록으로 덮어써서 잃는다.
    if (currentData === data) overlay?.setAvailableLangs(availableLangsForChip(data));
  }
  let pronApplied = false;
  data.lines.forEach((line, i) => {
    const t = translated[i]?.translation?.trim();
    // '[NO API KEY]'는 구버전 서버의 키 미설정 플레이스홀더 — 번역으로 표시하지 않는다.
    // `!line.translation` — 발음과 **같은 규칙(사람 우선)**이다. 이 가드가 발음에만 있어서,
    // 수동작성 ko 자막이 붙은 곡(yt-captions.mergeCaptionTranslation은 수동 트랙만 쓴다)에서
    // 사람이 옮긴 번역이 기계번역으로 조용히 교체됐다. 빈 줄만 채우면 사람 번역은 지키면서
    // 겹침 매칭에서 빠진 줄도 메워진다.
    if (t && t !== line.text && !t.startsWith('[NO API KEY]') && !line.translation) {
      line.translation = t;
    }
    // 발음표기(target=ko면 한글 독음) — 사람이 단 발음(보카로 위키)이 있으면 건드리지 않는다
    const p = translated[i]?.pronunciation?.trim();
    if (p && !line.pronunciation) {
      line.pronunciation = p;
      pronApplied = true;
    }
  });
  // 서버가 복구하지 못한 줄(응답 잘림 등)은 failed로 온다. 조용히 비워 두면 사용자는
  // 왜 그 줄만 번역이 없는지 알 수 없다 — 완료 알림까지 기다리지 않고 여기서 바로 말한다.
  const failed = translated.filter(tl => tl?.failed).length;
  overlay?.setTranslationStatus(
    failed > 0 ? t('content.translation.partialFailure', [String(failed)]) : null,
  );
  overlay?.refreshTranslations();
  // 발음이 새로 붙었으면 PiP 내부 변환 캐시(setLines 시점 복사)도 다시 채운다
  if (pronApplied && currentData === data) pip.setLines(data.lines);
  pip.refresh();
}

/** 원문 스크립트 추정 — 가나/한자가 실질적으로 있으면 ja, 한글이면 ko, 그 밖은 라틴 문자
 *  기준 en으로 추정한다(순서 중요: 가나·한자 우선 판정 — 일본어 곡 가사에도 라틴 단어가
 *  섞여 있어 라틴만 보면 en으로 오판한다). */
function detectSongScript(texts: string[]): 'ja' | 'ko' | 'en' {
  const joined = texts.join('');
  if ((joined.match(/[぀-ヿ㐀-鿿]/g)?.length ?? 0) >= 5) return 'ja';
  if ((joined.match(/[가-힣]/g)?.length ?? 0) >= 5) return 'ko';
  return 'en';
}

/**
 * 발음표기가 기대되는 곡인가 — 매트릭스: 곡 스크립트가 내 번역 언어와 다르면 기대한다
 * (ja곡×ko유저 = 기대, ja곡×ja유저 = 기대 안 함). translationLanguage가 zh면 어느 곡
 * 스크립트와도 일치하지 않으므로 항상 기대(발음은 hangul로 폴백 — 알려진 미결).
 *
 * 특례(en곡×ko유저) — 서버는 예전부터 en/ko 원문이면 발음 생성을 건너뛰어 왔고(대각선
 * 규칙이 생기기 전부터), 이 규칙은 target=ko일 때만 유지된다(en→ko 통째 음차는 서버에
 * 아직 없다 — ko_reading.py의 latin_to_kana는 ja/en 타깃용이지 ko 타깃용이 아니다).
 * 이 특례 없이 매트릭스만 적용하면 en곡+ko유저(오늘의 기본 조합)에서 서버가 절대 채우지
 * 않을 발음을 "기대함"으로 판정해, loadTranslations가 이미 완료된 번역도 매번 다시
 * 요청하는 회귀가 생긴다 — 실측으로 잡아 특례를 남겼다.
 */
function expectsPronunciation(texts: string[]): boolean {
  const script = detectSongScript(texts);
  const lang = settings.translationLanguage;
  if (script === lang) return false; // 대각선 — 번역·발음 둘 다 생략
  if (lang === 'ko' && script === 'en') return false; // en 원문 + target=ko 특례
  return true;
}

/**
 * 제목바 언어 칩의 «보유» 목록 — everyric 소스가 아니거나 서버가 available_langs 자체를
 * 안 주면(구서버) null(칩 줄 전체 숨김). 있으면 곡 자신의 스크립트 언어를 항상 합쳐 넣는다.
 *
 * 곡 자신의 언어는 번역 레이어가 **절대 생기지 않는다**(대각선 — expectsPronunciation의
 * `script === lang` 분기와 같은 이유로 서버가 번역 자체를 건너뛴다). 서버 목록만 그대로
 * 쓰면 이 언어 칩이 영원히 "미보유"로 보이는데, 클릭해도 실제로 생성되는 게 없고 그냥
 * 원문만 보기로 전환될 뿐이다(대각선 생략이 번역 줄을 자연히 비운다) — "미보유·클릭해
 * 생성" 문구를 달아 두면 사용자는 생성이 실패했다고 오해한다. 그래서 곡 언어는 항상
 * "보유" 스타일로 취급한다.
 */
function availableLangsForChip(data: LyricsData | null): string[] | null {
  if (!data || data.source !== 'everyric' || !data.availableLangs) return null;
  const songLang = detectSongScript(data.lines.map(l => l.text));
  return data.availableLangs.includes(songLang) ? data.availableLangs : [...data.availableLangs, songLang];
}

/**
 * 번역 캐시 키 — videoId·언어만으로는 **부족하다.**
 *
 * 번역 적용은 인덱스 위치로만 이뤄지므로(applyTranslations) 원문이 바뀌면 번역이 다른 줄에
 * 붙는다. 실제 경로: 자막 폴백 35줄로 번역을 캐시한 뒤 헤더 검색에서 LRCLIB 42줄 후보를
 * 고르면, 같은 영상·같은 언어라 캐시가 그대로 히트해 자막 기준 번역이 LRCLIB 줄 위에
 * 위치로 얹혔다. 키에 원문 지문을 넣어 **다른 원문의 번역은 히트조차 되지 않게** 한다.
 */
function translationKey(videoId: string, lang: string, srcLines: string[]): string {
  return `${videoId}:${lang}:${sourceFingerprint(srcLines)}`;
}

/** 원문 지문 — 줄 수 + FNV-1a 32bit. 해시는 1차 필터이고, 최종 방어는 적용 시점의
 *  줄 수 대조다(applyTranslations) — 지문이 같아도 줄 수가 어긋나면 적용하지 않는다. */
function sourceFingerprint(lines: string[]): string {
  const text = lines.join('\n');
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return `${lines.length}-${(hash >>> 0).toString(36)}`;
}

/** LRU 판독 — 조회한 항목을 최신으로 되돌려 넣어, 축출이 '가장 오래 안 쓴 것'부터 되게 */
function translationCacheGet(key: string): TranslatedLine[] | undefined {
  const v = translationCache.get(key);
  if (v !== undefined) {
    translationCache.delete(key);
    translationCache.set(key, v);
  }
  return v;
}

/** 서버 번역(발음 포함) 요청 — video+언어 기준으로 동시 요청을 하나로 합치고 캐시에 저장 */
function requestTranslation(
  videoId: string, srcLines: string[],
): Promise<TranslatedLine[] | undefined> {
  // 키에 원문 지문이 들어가므로, 같은 영상이라도 원문이 다르면 다른 요청·다른 캐시 항목이다
  const key = translationKey(videoId, settings.translationLanguage, srcLines);
  const inFlight = pendingTranslate.get(key);
  if (inFlight) return inFlight;
  const p = (async () => {
    const res = await sendToBackground<TranslateResult>({
      type: 'TRANSLATE',
      payload: {
        text: srcLines.join('\n'),
        targetLang: settings.translationLanguage,
        title: currentSong?.title,
        artist: currentSong?.artist ?? undefined,
        // 최초 호출에 실어 보내 서버가 이 언어 레이어로 저장하게 한다 — 성공하면 다음
        // 조회부터는(lang 쿼리 배선 이후) 재번역 없이 서버가 바로 이 결과를 돌려준다
        persist: true,
        videoId,
      },
    });
    noteFailure(res.failure); // 번역은 서버 전용 경로 — 실패 사유를 상태로 남긴다
    const lines = res.data?.lines;
    if (lines && lines.length > 0) {
      translationCache.set(key, lines);
      // 장시간 세션 메모리 상한 — 가장 오래된 항목부터 축출 (Map은 삽입 순서 유지)
      while (translationCache.size > 24) {
        const oldest = translationCache.keys().next().value;
        if (oldest === undefined) break;
        translationCache.delete(oldest);
      }
    }
    return lines && lines.length > 0 ? lines : undefined;
  })().finally(() => pendingTranslate.delete(key));
  pendingTranslate.set(key, p);
  return p;
}

/** LLM 번역·한글 독음을 받아 line_meta로 변환 — 캐시 우선, 실패 시 undefined(원문 정렬 폴백).
 *  LLM이 echo한 original 대신 넘겨받은 원문으로 인덱스 매핑한다 (서버 병합은 텍스트 매칭이라
 *  원문이 정확해야 하고, 서버 번역도 같은 규칙으로 줄을 나누므로 인덱스가 일치). */
async function fetchLlmLineMeta(
  videoId: string, srcLines: string[],
): Promise<{ text: string; pronunciation?: string; translation?: string }[] | undefined> {
  const lang = settings.translationLanguage;
  try {
    overlay?.setTranslationStatus(t('content.translation.aiGenerating'));
    let translated = translationCacheGet(translationKey(videoId, lang, srcLines));
    // 발음이 빠진 캐시(구버전 응답 등)는 다시 받아온다
    if (
      !translated || translated.length !== srcLines.length
      || (expectsPronunciation(srcLines) && !translated.some(l => l.pronunciation))
    ) {
      translated = await requestTranslation(videoId, srcLines);
    }
    if (translated && translated.length > 0) {
      return srcLines
        .map((t, i) => ({
          text: t,
          pronunciation: translated![i]?.pronunciation?.trim() || undefined,
          translation: translated![i]?.translation?.trim() || undefined,
        }))
        .filter(m => m.pronunciation || m.translation);
    }
  } catch { /* 번역 실패 — 메타 없이 진행 */ } finally {
    if (videoId === currentVideoId) overlay?.setTranslationStatus(null);
  }
  return undefined;
}

async function searchLyrics(queryOverride?: { title: string; artist: string }): Promise<void> {
  const videoId = currentVideoId;
  if (!videoId) return;
  const seq = ++searchSeq;
  const panel = ensureOverlay();
  panel.setVisible(true);
  panel.showLoading();
  if (pip.isOpen()) pip.showPanelLoading(); // PiP도 같은 검색 상태를 따라간다
  updateGenChip(); // 이 영상(또는 다른 영상)의 전사 진행 칩은 검색과 무관하게 유지
  // 알림 칩은 영상별 사건이다 — **영상이 바뀔 때만** 지난 알림을 지우고, 이 영상의 검증이
  // 아직 돌고 있으면 배지를 되살린다(돌아왔을 때도 진행 중임을 알 수 있게).
  // 같은 영상의 재조회에서 지우면 안 된다: 자동 연결 성공은 "알림 → 재조회" 순서라
  // 여기서 무조건 지우면 방금 띄운 '자동 연결됨'을 스스로 삭제한다.
  if (noticeVideoId !== videoId) {
    noticeVideoId = videoId;
    showNotice(linkJobs.has(videoId) ? t('content.linkProbe.chip') : null);
  }
  engine.stop();

  void refreshServerStatus();

  let song: SongInfo | null;
  if (queryOverride) {
    song = {
      title: queryOverride.title,
      artist: queryOverride.artist || null,
      videoId,
      duration: currentSong?.duration ?? Math.round(getVideoElement()?.duration ?? 0),
    };
  } else {
    song = await waitForSongInfo(seq);
  }
  if (seq !== searchSeq || videoId !== currentVideoId) return;

  // 곡 인식 시점엔 video 메타데이터가 아직 없을 수 있음 — duration 없이 LRCLIB에
  // 조회하면 길이가 다른 버전이 매칭될 수 있으므로 한 번 더 읽는다
  if (song && song.duration === 0) {
    const d = getVideoElement()?.duration;
    song.duration = d && Number.isFinite(d) ? Math.round(d) : 0;
  }

  if (!song) {
    // 곡 인식 실패도 "이 영상엔 가사가 없다"와 같은 상태 — 이전 곡의 가사/노트/오프셋/PiP가
    // 남지 않도록 성공 경로와 같은 리셋(applyLyricsData(null))을 태운다
    currentSong = null;
    panel.setSong(null);
    applyLyricsData(null);
    return;
  }
  currentSong = song;
  panel.setSong(song);
  // PiP 제목도 함께 갱신한다 — 지금까지 PiP는 **창을 열 때 한 번만** 제목을 받아서, 곡을
  // 넘겨도 이전 제목이 남았다(열던 순간 광고가 돌고 있었다면 광고 제목이 계속 남는다)
  if (pip.isOpen()) pip.setSong(song.title, song.artist ?? '');

  // 소스 우선순위: 서버 싱크는 항상 최우선, 그 다음은 설정에 따라
  // 보카로 위키(발음·사람 번역) → LRCLIB 순서 또는 그 반대
  const wikiFirst = settings.lyricsSourcePriority === 'vocaro';
  const res = await sendToBackground<LyricsData | null>({
    type: 'FETCH_LYRICS',
    // lang은 번역 레이어 언어별 서빙 요청용 — background가 아직 서버 호출에 넘기지 않으면
    // (구버전 배선) 조용히 무시되고 오늘과 동일하게 동작한다
    payload: { ...song, skipLrclib: wikiFirst, lang: settings.translationLanguage },
  });
  if (seq !== searchSeq || videoId !== currentVideoId) return;
  // 서버 조회가 실패했다면(401·연결 불가 등) 사유를 먼저 상태에 반영한다 — 그래야
  // 아래 applyLyricsData(null)이 "가사를 찾지 못했어요" 대신 서버 문제 화면을 띄운다
  const lookupFailure = noteFailure(res.failure);
  if (res.error) {
    // 서버가 흔들려도 이전 곡 상태가 남으면 안 된다 — 먼저 리셋하고 오류 문구로 덮어쓴다
    // (currentSong은 인식에 성공한 새 곡이므로 유지)
    applyLyricsData(null);
    const note = failureNote(lookupFailure);
    panel.showError(t('content.error.lyricsLoadFailed'), note);
    if (pip.isOpen()) pip.showPanelError(t('content.error.lyricsLoadFailed'), note);
    return;
  }

  let data = res.data ?? null;
  if (data?.synced) keptLyrics = null; // 새 싱크가 생겼으니 초기화 보관본은 낡았다
  currentSourceUrl = null;
  if (!data) {
    const wiki = await lookupWikiSources(videoId, seq, song.title);
    if (wiki.stale) return;
    data = wiki.data;
  }
  // 위키 우선 모드에서 위키까지 미스면 후순위 LRCLIB 시도
  if (!data && wikiFirst) {
    const lr = await sendToBackground<LyricsData | null>({ type: 'FETCH_LRCLIB', payload: song });
    if (seq !== searchSeq || videoId !== currentVideoId) return;
    data = lr.data ?? null;
  }
  // 서버 싱크(위키 가사로 생성된 것)에 위키의 발음/사람 번역을 텍스트 매칭으로 병합
  if (data && data.source === 'everyric' && data.synced) {
    await enrichFromVocaro(videoId, data);
    if (seq !== searchSeq || videoId !== currentVideoId) return;
  }
  // 초기화 직후라면 방금 지운 가사를 자막보다 **먼저** 되돌린다 — 정확한 원문이 이미
  // 손에 있는데 자동 생성 자막으로 갈아타는 것은 어떤 경우에도 개선이 아니다
  if (!data && keptLyrics?.videoId === videoId) data = keptLyrics.data;
  // 어디에도 가사가 없으면 마지막으로 영상 자체 자막을 띄워 본다
  if (!data) {
    data = await tryCaptionFallback(videoId, song);
    if (seq !== searchSeq || videoId !== currentVideoId) return;
  }
  applyLyricsData(data);

  // 서버 싱크가 없는 영상(=조회가 found:false였던 경우)에서만 같은 곡 후보를 물어본다.
  // 이미 싱크가 있는 다수 케이스에는 요청이 아예 나가지 않아 지연이 없다. 화면을 먼저
  // 그린 뒤에 부르므로 후보 탐색이 가사 표시를 늦추지도 않는다.
  // (LRCLIB 일반 가사·자막 폴백으로 화면이 차 있어도 부른다 — 원곡의 싱크·발음·음정을
  //  빌려오는 것이 더 나은 결과이고, 서버가 아니라고 판단하면 아무 일도 일어나지 않는다.)
  if (!(data?.source === 'everyric' && data.synced)) {
    void probeLinkCandidates(videoId, song);
  }
}

/** 위키 조회 결과를 LyricsData로 변환하고 출처·재병합 캐시를 채운다 */
function adoptVocaroResult(videoId: string, vocaro: VocaroResult): LyricsData {
  const lines: LyricLine[] = vocaro.lines.map(l => ({
    time: null,
    endTime: null,
    text: l.text,
    translation: l.translation,
    pronunciation: l.pronunciation,
  }));
  currentSourceUrl = vocaro.pageUrl;
  // 이 곡의 위키 페이지를 기억 — 싱크 생성 뒤에도 발음/번역을 다시 입힐 수 있게.
  // 타임스탬프를 함께 저장해 오래 안 본 영상부터 정리할 수 있게 한다
  lastVocaro = { videoId, lines: vocaro.lines };
  try {
    void chrome.storage.local
      .set({ [`vocaroRef:${videoId}`]: { slug: vocaro.slug, t: Date.now() } })
      .then(() => pruneVocaroRefs());
  } catch { /* 저장 실패는 무시 — 세션 내 캐시로도 동작 */ }
  return { source: 'vocaro', synced: false, lines, plainText: lines.map(l => l.text).join('\n') };
}

/** SourceResult(위키 등 소스 어댑터 조회 결과)의 출처 표기 — 소스별 사람이 읽는 이름. */
function attributionFromSource(result: SourceResult): SourceAttribution {
  const name = result.sourceId === 'miraheze'
    ? `${result.pageTitle} — VocaloidLyrics Wiki`
    : '보카로 가사 위키';
  return { name, url: result.pageUrl, license: result.license, sourceId: result.sourceId };
}

/**
 * SourceResult를 LyricsData로 — vocaro **외** 소스(miraheze 등) 채택 경로.
 *
 * vocaro 직접 조회는 재입힘 캐시(lastVocaro/vocaroRef)가 딸린 ``adoptVocaroResult``를
 * 그대로 쓴다(건드리지 않는다 — 그 경로는 그대로 신뢰). ``source``를 여기서도 'vocaro'로
 * 두는 이유: LyricsSource 타입은 오버레이 배지 등 손대지 않는 파일이 이미 소비하고 있어
 * 새 값을 추가하면 그 파일들이 모르는 값으로 새어 배지가 깨진다(LRCLIB로 오표시) — 대신
 * ``attribution.sourceId``로 실제 출처를 구분한다(정확한 이름·라이선스는 attribution에
 * 있으므로 배지 옆에 그대로 병기된다).
 *
 * ``pronLang``이 'hangul'이 아니면(로마자 등) 레거시 ``pronunciation`` 필드는 비운다 —
 * 그 필드는 한글 전용 계약이다(LyricLine 문서). 대신 ``pron[script]`` 딕셔너리에 실어
 * lib/lang.ts의 resolvedPronunciation이 사용자의 script 설정과 일치할 때만 보여주게 한다
 * (한글을 기대하는 사용자에게 로마자가 새지 않는다). 이 설계 덕에 생성 시 line_meta도
 * 자동으로 올바르게 비워진다 — handleGenerate가 읽는 것은 이 pronunciation 필드다.
 */
function adoptSourceResult(result: SourceResult): LyricsData {
  const pronKey = result.pronLang;
  const lines: LyricLine[] = result.lines.map(l => ({
    time: null,
    endTime: null,
    text: l.text,
    translation: l.translation,
    pronunciation: pronKey === 'hangul' ? l.pronunciation : undefined,
    pron: pronKey && pronKey !== 'hangul' && l.pronunciation ? { [pronKey]: l.pronunciation } : undefined,
  }));
  currentSourceUrl = result.pageUrl;
  return {
    source: 'vocaro',
    synced: false,
    lines,
    plainText: lines.map(l => l.text).join('\n'),
    attribution: attributionFromSource(result),
  };
}

/**
 * 위키 소스 체인 — vocaro·miraheze를 우선순위대로 시도해 첫 성공을 채택한다.
 *
 * 우선순위는 번역 언어 기준: ko면 vocaro(한글 발음·한국어 번역)를 먼저, 아니면
 * miraheze(로마자 발음·영어 번역)를 먼저 — 사용자 언어와 같은 위키를 우선한다.
 *
 * 두 소스 다 순차 호출이라 지연이 있다 — searchLyrics 스스로도 매 await 뒤에 최신
 * 검색인지 확인하지만, 여기 안에서도 각 조회 뒤에 확인해 자리를 옮긴 사용자에게 갈 늦은
 * 응답을 조기에 끊는다. ``stale``이 true면 호출부는 즉시 return해야 한다(그 자리에서
 * 이어 쓰면 낡은 seq의 부작용 — LRCLIB 폴백·applyLyricsData 등 — 이 새 검색 위에 겹친다).
 */
async function lookupWikiSources(
  videoId: string, seq: number, title: string,
): Promise<{ stale: true } | { stale: false; data: LyricsData | null }> {
  const order: ('vocaro' | 'miraheze')[] =
    settings.translationLanguage === 'ko' ? ['vocaro', 'miraheze'] : ['miraheze', 'vocaro'];
  for (const src of order) {
    if (src === 'vocaro') {
      const vocaro = await sendToBackground<VocaroResult | null>({
        type: 'VOCARO_LOOKUP',
        payload: { title },
      });
      if (seq !== searchSeq || videoId !== currentVideoId) return { stale: true };
      if (vocaro.data && vocaro.data.lines.length > 0) {
        return { stale: false, data: adoptVocaroResult(videoId, vocaro.data) };
      }
    } else {
      const miraheze = await sendToBackground<SourceResult | null>({
        type: 'MIRAHEZE_LOOKUP',
        payload: { title },
      });
      if (seq !== searchSeq || videoId !== currentVideoId) return { stale: true };
      if (miraheze.data && miraheze.data.lines.length > 0) {
        return { stale: false, data: adoptSourceResult(miraheze.data) };
      }
    }
  }
  return { stale: false, data: null };
}

/** vocaroRef가 시청 이력만큼 무한히 쌓이지 않게 오래된 것부터 정리.
 *  타임스탬프 없는 구형(문자열) 항목은 가장 오래된 것으로 취급한다. */
const VOCARO_REF_MAX = 120;
async function pruneVocaroRefs(): Promise<void> {
  try {
    const all = await chrome.storage.local.get(null);
    const refs = Object.entries(all)
      .filter(([k]) => k.startsWith('vocaroRef:'))
      .map(([k, v]) => ({
        key: k,
        t: typeof v === 'object' && v !== null ? ((v as { t?: number }).t ?? 0) : 0,
      }));
    if (refs.length <= VOCARO_REF_MAX) return;
    refs.sort((a, b) => a.t - b.t);
    await chrome.storage.local.remove(refs.slice(0, refs.length - VOCARO_REF_MAX).map(r => r.key));
  } catch { /* 정리 실패는 무시 */ }
}

/**
 * 후보 조회 순번 — **가장 최근에 발사한 조회의 응답만 화면에 그린다.**
 *
 * `searchSeq`(가사 검색·생성 흐름)와 **일부러 분리했다.** 그것을 올리면 진행 중인 가사
 * 로딩까지 폐기되는데(handlePickCandidate가 그 목적으로 쓴다), 후보 목록을 새로 조회하는
 * 것은 그런 뜻이 아니다.
 *
 * 왜 필요한가: `openSearch()`는 검색 시트를 열면 **즉시** 유튜브 원본 제목으로 자동 조회를
 * 쏜다(overlay.ts). 원본 제목은 보통 지저분해서(`熱異常 / いよわ - むﾄ (cover)`) 어느
 * 소스와도 매칭되지 않는다. 사용자가 그 직후 제목을 다듬어 다시 검색하면 — 패널이 바로
 * 그렇게 하라고 권한다 — 두 조회가 독립적으로 날아가고, **늦게 도착한 쪽이 화면을 덮어쓴다.**
 * LRCLIB 응답이 0.9~3.1초로 들쭉날쭉해 어느 쪽이 늦는지가 매번 달라진다.
 *
 * 실측: 8회 시도 중 7회가 "결과가 없어요"였고, 그중 사용자 쿼리는 실제로 맞는 후보를
 * 찾아냈는데도(vocaro-match found:true) 자동 조회의 빈 결과가 화면에 남았다. 같은 코드가
 * 타이밍에 따라 성공하기도 해서(늦게 끝난 회차는 정상 후보 3개로 안착) 순수 경쟁 조건이다.
 * 백엔드는 44회 요청 전부 쿼리에 정확히 부합하는 응답을 줬다 — 잘못된 것은 이쪽뿐이다.
 */
let candidateSearchSeq = 0;

/** 수동 검색: 소스별 후보 리스트를 모아 패널에 전달 */
async function handleCandidateSearch(query: { title: string; artist: string }): Promise<void> {
  const videoId = currentVideoId;
  if (!videoId) return;
  const seq = ++candidateSearchSeq;
  const res = await sendToBackground<SearchCandidate[]>({
    type: 'SEARCH_CANDIDATES',
    payload: { ...query, duration: currentSong?.duration ?? 0 },
  });
  // 낡은 조회의 응답은 성공이든 실패든 버린다 — 실패 알림도 마찬가지다. 사용자가 이미
  // 다른 검색어로 넘어간 뒤에 옛 조회의 오류를 띄우면 방금 누른 검색이 실패한 것처럼 보인다.
  if (videoId !== currentVideoId || seq !== candidateSearchSeq) return;
  // 실패를 빈 배열로 접으면 패널이 "결과가 없어요 — 제목을 줄여 보세요"를 띄운다. 서버가
  // 죽었거나 키가 틀린 것을 검색어 탓으로 돌리는 셈이고, 사용자는 틀린 행동을 반복한다.
  // 사용자가 직접 누른 버튼이니 실패는 실패라고 말한다.
  if (!res.data) {
    showNotice(
      t('content.error.candidatesLoadFailed', [failureNote(noteFailure(res.failure)) ?? res.error ?? t('content.error.unknown')]),
      8000,
    );
    return;
  }
  ensureOverlay().showSearchResults(res.data);
}

/** 후보 선택: 해당 소스에서 가사를 받아 현재 가사를 교체한다 (잘못 가져온 가사 롤백 경로) */
async function handlePickCandidate(candidate: SearchCandidate): Promise<void> {
  const videoId = currentVideoId;
  if (!videoId) return;
  const seq = ++searchSeq; // 진행 중이던 자동 검색/생성 흐름은 폐기
  removeJob(videoId); // 다른 가사를 고르면 이 영상의 기존 전사 추적은 버린다
  // 이전 가사 기준으로 받아 둔 번역·발음도 버린다 — 키에 원문 지문이 들어가 오적용은
  // 이미 막히지만, 갈아탄 원문의 캐시를 들고 있을 이유가 없다(다시 쓸 일이 없는 항목이
  // LRU 자리만 차지한다). 후보 교체는 "이 가사가 아니었다"는 사용자의 선언이다.
  for (const key of [...translationCache.keys()]) {
    if (key.startsWith(`${videoId}:`)) translationCache.delete(key);
  }
  updateGenChip();
  engine.stop();
  const panel = ensureOverlay();
  panel.showLoading(t('content.loading.selectedCandidate'));

  let data: LyricsData | null = null;
  currentSourceUrl = null;
  if (candidate.source === 'vocaro') {
    const page = await sendToBackground<VocaroResult | null>({
      type: 'VOCARO_PAGE',
      payload: { slug: candidate.slug },
    });
    if (seq !== searchSeq || videoId !== currentVideoId) return;
    if (page.data && page.data.lines.length > 0) data = adoptVocaroResult(videoId, page.data);
  } else {
    const res = await sendToBackground<LyricsData | null>({
      type: 'PICK_LRCLIB',
      payload: { id: candidate.id },
    });
    if (seq !== searchSeq || videoId !== currentVideoId) return;
    data = res.data ?? null;
  }

  if (!data) {
    // 후보를 못 불러온 것이 **보던 가사를 잃을 이유는 아니다.** showLoading으로 이미 접힌
    // 이전 가사를 되돌리고(검색 시트의 '← 보던 가사로 돌아가기'와 같은 복귀 경로) 실패는
    // 칩으로만 말한다 — 예전에는 오류 화면이 이전 가사를 통째로 버렸다.
    applyLyricsData(currentData);
    showNotice(t('content.notice.candidateLoadFailed'), 12000);
    return;
  }
  applyLyricsData(data);
}

function applyLyricsData(data: LyricsData | null): void {
  const panel = ensureOverlay();
  currentData = data;
  lastLineIndex = -1;
  engine.stop();
  // 영상별 저장 오프셋 복원 (서버에 저장된 값, 없으면 0) — UI 라벨도 함께
  videoOffset = data?.userOffset ?? 0;
  panel.setOffsetValue(videoOffset);
  karaokeAudio.setOffset(videoOffset);
  // 번역 상태 문구는 **그 곡의** 진행/실패 보고다 — 곡이 바뀌면 반드시 버린다.
  // 예전에는 곡이 바뀔 때 loadTranslations·fetchLlmLineMeta가 그냥 return하면서 문구를
  // 비우지 않았고(finally도 videoId가 아직 현재일 때만 지운다), 문구 자리가 푸터라
  // resetBody()도 건드리지 않아 B의 푸터에 "번역·발음 생성 중…"이 영구히 남았다.
  // 아래에서 이 곡의 번역이 다시 시작되면(loadTranslations) 문구는 그때 새로 쓰인다.
  panel.setTranslationStatus(null);
  // 멜로디·메트로놈도 **지금 화면의 곡**을 따라야 한다. 갱신이 「싱크 있음 + PiP 열림」
  // 분기에만 있었고 비우는 곳은 cleanupForPage뿐이라, 멜로디를 켠 채 가사 없는 곡·플레인
  // 가사 곡으로 넘어가면 이전 곡의 노트와 BPM이 새 곡 위에서 계속 울렸다. 곡이 바뀌는
  // 지점이 여기 하나이므로 여기서 한 번에 맞춘다 (타이밍이 없는 가사는 노트도 없다).
  karaokeAudio.setNotes(data?.synced ? collectMelodyNotes(data.lines) : []);
  karaokeAudio.setTempo(data?.synced ? data.tempo ?? null : null);
  // 곡 전체 정렬 신뢰도가 매우 낮으면 경고 바 (설정으로 끌 수 있음)
  panel.setQualityWarning(
    settings.lowConfWarning && data?.synced && data.source === 'everyric'
      && data.qualityScore != null && data.qualityScore < 0.001
      ? data.qualityScore
      : null,
  );
  const attribution = data?.attribution
    ?? (data?.source === 'vocaro' ? { name: '보카로 가사 위키', url: currentSourceUrl } : null);
  panel.setAttribution(attribution ?? null);
  // 다른 영상 싱크를 빌려온 상태면 출처 배지·검색 시트 해제 UI에 반영
  panel.setLinked(data?.source === 'everyric' ? data.linked ?? null : null);
  // 제목바 언어 칩 — everyric 소스에만 의미가 있다(availableLangs는 서버 번역 레이어 목록,
  // 곡 자신의 언어는 availableLangsForChip이 항상 합쳐 넣는다 — 대각선 칩 참고).
  // 곡이 바뀌면 이전 곡에 걸려 있던 로딩 표시도 함께 지운다.
  panel.setAvailableLangs(availableLangsForChip(data));
  panel.setLangPending(null);

  if (!data) {
    // 싱크가 없다고 PiP를 닫지 않는다 — 재생목록을 돌리다 가사 없는 곡이 나오면
    // 창이 증발해 매번 브라우저 창으로 돌아가야 했다. 같은 패널 조각을 PiP 안에
    // 띄워 거기서 바로 검색·붙여넣기·생성 요청을 할 수 있게 한다.
    if (pip.isOpen()) {
      // 패널은 스테이지를 덮을 뿐 **비우지는 않는다** — setLines를 안 하면 이전 곡의 가사·
      // 노트가 그대로 남아, 좌상단 패널 토글로 새 영상 위에서 A의 스테이지가 다시 나왔다
      pip.setLines([]);
      pip.showPanelEmpty(currentSong);
    }
    refreshPipMirror(); // 가사가 없어도 창은 살아 있다 — 영상만 이전 곡에 멈춰 있으면 안 된다
    panel.showEmpty(currentSong);
    return;
  }
  // 자동 생성 자막은 싱크 생성의 원문으로 쓸 수 없다(handleGenerate가 막는다) — 배너에
  // 버튼 대신 사유를 띄워, 눌러 보고 거절당하는 경험을 만들지 않는다
  const generateBlocked = data.source === 'caption' && data.captionAuto
    ? t('content.generate.blockedAutoCaption')
    : undefined;

  if (data.synced) {
    if (pip.isOpen()) {
      // 검색을 시작할 때 띄운 패널(pip.showPanelLoading)을 반드시 접는다 — 레인 표시
      // 조건에 !panelActive가 들어 있어, 안 접으면 싱크가 도착해도 가라오케가 닫힌
      // 채로 남는다(영상을 넘길 때마다 가라오케가 풀리는 증상의 원인이었다).
      pip.clearPanel();
      pip.setTempo(data.tempo ?? null);
      pip.setKey(data.key ?? null);
      pip.setDebugMeta(data.debugMeta ?? null);
      panel.setDebugMeta(data.debugMeta ?? null);
      pip.setShowF0(settings.pitchF0Curve);
      pip.setLines(data.lines);
      // 노트·템포는 위에서 이미 이 곡 값으로 맞췄다 (분기마다 갱신하던 것을 한곳으로 모았다)
      if (settings.pipKeepPanel) {
        panel.showSyncedLyrics(data.lines, data.source, data.plainText, generateBlocked);
        panel.setPipEnabled(PipController.isSupported());
      } else {
        panel.showPipPlaceholder();
      }
      panel.setPipActive(true);
    } else {
      panel.showSyncedLyrics(data.lines, data.source, data.plainText, generateBlocked);
      panel.setPipEnabled(PipController.isSupported());
    }
    void startEngine(data.lines);
  } else {
    // 싱크 없는 플레인 가사도 PiP를 유지한 채 창 안에 보여준다
    if (pip.isOpen()) {
      // 타이밍이 없는 가사는 스테이지·레인에 그릴 것이 없다 — 비워야 이전 곡 가사가
      // 패널 뒤에 남지 않는다(토글 버튼도 함께 사라져 빈 스테이지로 갈 길이 막힌다)
      pip.setLines([]);
      pip.showPanelPlain(data.lines, data.plainText);
    }
    refreshPipMirror();
    panel.showPlainLyrics(data.lines, data.source, data.plainText);
  }
  if (settings.showTranslation) void loadTranslations();
  pushDebug(null);
}

function makeEngineHandlers(): SyncHandlers {
  return {
    onLineChange: index => {
      lastLineIndex = index;
      overlay?.highlightLine(index);
      pip.update(index);
    },
    onTick: time => {
      overlay?.updateTime(time);
      pip.tick(time, engine.getDuration(), engine.isPaused());
      const video = engine.getVideo();
      if (video) {
        pip.updateVolume(video.volume, video.muted);
        // 마이크 궤적의 벽시계→곡 시간 환산에 배속이 필요하다
        pip.setPlaybackRate(video.playbackRate);
      }
      if (settings.debugInfo && Date.now() - lastDebugPush >= 500) {
        lastDebugPush = Date.now();
        pushDebug(time);
      }
    },
  };
}

async function startEngine(lines: LyricLine[]): Promise<void> {
  const video = await waitForVideo();
  if (!video || !currentData?.synced) return;
  engine.start(video, lines, makeEngineHandlers());
  engine.setOffset(videoOffset);
  // PiP 영상은 captureStream() 미러라 곡이 바뀌면 재부착해야 새 프레임이 흐른다.
  // engine이 바인딩하는 video가 바뀔 때마다 PiP도 따라간다는 불변식을 여기서 보장한다
  // (watchVideoBinding은 engine.start가 이미 갱신해버려 이 전환을 못 잡는다).
  refreshPipMirror(video);
}

/**
 * PiP 영상 미러를 지금 페이지의 video에 다시 붙인다.
 *
 * 미러는 captureStream()이라 영상이 바뀌면 이전 트랙이 끝나 마지막 프레임에서 멈춘다.
 * 싱크가 있는 곡은 startEngine이 재부착하지만, 싱크가 없는 곡·조회 실패는 engine을
 * 시작하지 않아 아무도 재부착하지 않았다 — PiP에 이전 곡의 정지 화면이 남는다.
 */
function refreshPipMirror(video?: HTMLVideoElement): void {
  const target = video ?? getVideoElement();
  if (!target) return;
  bindMirrorRefresh(target); // PiP가 닫혀 있어도 걸어 둔다 — 리스너가 발화 시점에 다시 확인한다
  if (pip.isOpen() && settings.pipShowVideo) pip.attachVideo(target);
}

/**
 * 이 video에 소스 교체 리스너를 한 번만 건다 — 미러 재부착 + 곡 제목 재판독.
 *
 * 광고↔본편처럼 **엘리먼트는 그대로인데 소스만 바뀌는** 전환에서는 watchVideoBinding이
 * 아무것도 못 잡는다(엘리먼트 동일성만 본다). 그때 captureStream 트랙이 끝나 PiP에는
 * 정지 프레임이 남는다. loadeddata만 듣는다 — playing까지 듣으면 일시정지 후 재생마다
 * 미러가 다시 붙어 화면이 깜빡인다.
 *
 * 같은 신호로 제목도 다시 읽는다: 두 문제의 원인이 같은 사건(이 엘리먼트의 소스가 바뀜)이다.
 */
const mirrorBound = new WeakSet<HTMLVideoElement>();
function bindMirrorRefresh(video: HTMLVideoElement): void {
  if (mirrorBound.has(video)) return;
  mirrorBound.add(video);
  video.addEventListener('loadeddata', () => {
    if (pip.isOpen() && settings.pipShowVideo) pip.attachVideo(video);
    refreshSongTitle();
  });
  // 제목은 'playing'에서도 한 번 더 읽는다 — loadeddata 시점에 mediaSession이 이미 본편
  // 메타로 갱신돼 있는지는 **확인하지 못했다**(광고를 재현하지 못했다). 늦게 채워지면
  // loadeddata 재판독이 광고 제목을 다시 잡아 수정이 헛돌기 때문에 신호를 하나 더 둔다.
  // refreshSongTitle은 값이 같으면 아무 일도 하지 않으므로(멱등) 재생·일시정지 반복에
  // 부작용이 없다. **미러는 여기 태우지 않는다** — 매 재생마다 다시 붙어 화면이 깜빡인다.
  video.addEventListener('playing', () => refreshSongTitle());
}

/**
 * 곡 제목·아티스트만 다시 읽는다 — **조회도 잡도 건드리지 않는다.**
 *
 * 왜 필요한가: detectSong()은 navigator.mediaSession.metadata를 우선하는데 **광고 중에는
 * 그것이 광고 메타**다. 그 순간 검색이 돌면 광고 제목이 currentSong에 굳고, checkCurrentPage는
 * videoId가 같으면 조기 반환하므로 광고가 끝난 뒤 다시 읽을 기회가 없었다 — 실측 2회로
 * 제목이 광고("홈키파홈매트… — Henkel Consumer Brand Korea", "29 Halmeoni 16x9 15s")로
 * 남았다(가사는 정상 92줄이었다: 조회는 videoId로 하므로 제목과 무관하다).
 *
 * 왜 **다시 조회하지 않는가**: 같은 영상에서 searchLyrics를 새로 발사하면 searchSeq가 올라
 * 진행 중인 검색 응답이 버려지고, 서버 요청도 공짜가 아니다. 반면 제목은 갱신 가치가 크다 —
 * 화면 표시뿐 아니라 생성·재생성 때 **싱크에 새겨져 커버 매칭의 유일한 단서**가 되므로,
 * 광고 제목이 저장되면 그 곡의 후보 탐색이 영구히 어긋난다.
 *
 * duration은 덮지 않는다: 광고 중 읽으면 광고 길이(15초 등)라, 이미 가진 본편 길이를
 * 그것으로 갈아치우면 LRCLIB 후보 매칭이 망가진다.
 */
function refreshSongTitle(): void {
  const videoId = currentVideoId;
  if (!videoId || videoId !== getCurrentVideoId()) return; // 이동 중이면 새 조회가 알아서 읽는다
  const info = detectSong();
  if (!info?.title || info.videoId !== videoId) return;
  if (
    currentSong
    && info.title === currentSong.title
    && (info.artist ?? '') === (currentSong.artist ?? '')
  ) return; // 바뀐 게 없으면 아무것도 하지 않는다
  currentSong = { ...info, duration: currentSong?.duration || info.duration };
  overlay?.setSong(currentSong);
  if (pip.isOpen()) pip.setSong(currentSong.title, currentSong.artist ?? '');
}

async function waitForVideo(maxRetries = 10, delayMs = 500): Promise<HTMLVideoElement | null> {
  for (let i = 0; i < maxRetries; i++) {
    const video = getVideoElement();
    if (video) return video;
    await sleep(delayMs);
  }
  return null;
}

async function waitForSongInfo(seq: number, maxRetries = 6, delayMs = 700): Promise<SongInfo | null> {
  for (let i = 0; i < maxRetries; i++) {
    if (seq !== searchSeq) return null; // 새 검색이 시작됨 — 즉시 중단
    const info = detectSong();
    if (info?.title) return info;
    await sleep(delayMs);
  }
  return detectSong();
}

// ── 싱크 생성 ───────────────────────────────────────────────────

async function handleGenerate(lyricsText: string, attributionName?: string): Promise<void> {
  const videoId = currentVideoId;
  const seq = searchSeq;
  // 파트 표기·주석 줄을 먼저 걷어낸다 — 모든 생성 경로가 지나는 한 지점.
  // 붙여넣기 UI는 자기 화면에서 이미 같은 필터를 통과시키고 무엇을 걸렀는지 보여줬으므로
  // (panels.buildPasteSection) 여기서는 보통 아무것도 남지 않는다(멱등). 배너의 'AI 전사
  // 생성'처럼 붙여넣기 UI를 거치지 않은 경로만 아래 칩으로 알린다.
  const cleaned = stripPartMarkers(lyricsText);
  // 검색 복사 가사(원문/독음/번역 3줄 반복) 감지 — 원문만 정렬에 쓰고
  // 독음·번역은 line_meta로 재활용한다 (LLM 번역 호출도 생략)
  const tri = parseTriLineLyrics(cleaned.text);
  // 빈 줄·앞뒤 공백을 걷어낸 실제 라인만 서버로 — LLM line_meta도 같은 배열로
  // 만들어 인덱스가 어긋나지 않게 한다 (서버 병합은 텍스트 매칭)
  const srcLines = tri
    ? tri.map(t => t.text)
    : cleaned.text.split('\n').map(s => s.trim()).filter(Boolean);
  if (!videoId || srcLines.length === 0) return;
  if (srcLines.length > 500) {
    // 입력 검증은 **입력을 지우면서** 말할 것이 아니다 — 오류 화면(showError→resetBody)은
    // 방금 붙여넣은 본문까지 날려, 사용자는 줄을 줄이려 해도 다시 옮겨 적어야 했다.
    // 여기서는 화면 상태와 무관하게 늘 칩으로만 알린다 (줄일 대상이 화면에 남아 있어야 한다).
    showNotice(t('content.generate.tooManyLines', [String(srcLines.length)]), 15000);
    return;
  }
  const text = srcLines.join('\n');

  // 화면의 자막으로 생성하는 것인지, 다른 가사(붙여넣기·검색 결과)로 생성하는 것인지를
  // **넘어온 텍스트로** 가른다. source만 보고 판단하면, 자막이 떠 있는 상태에서 정확한
  // 원문을 붙여넣어도 붙여넣은 가사가 조용히 버려지고 자막이 대신 쓰인다.
  const captionText = currentData?.source === 'caption'
    ? currentData.lines.map(l => l.text).join('\n')
    : null;
  const fromCaption = captionText !== null && text === captionText;

  // 자동 생성(ASR) 자막은 화면 표시까지만 허용한다 — 노래를 ASR로 받아 적으면
  // 「縋って いつも縋って」가 「すがっていつもすがって おち読も」로 나온다(실측).
  // 그 텍스트로 만든 싱크는 서버에 저장돼 모든 사용자의 원문이 되고, 원문이 틀렸으니
  // 발음·번역도 의미가 없어진다. 화면을 지우지 않고 사유만 알린다.
  if (fromCaption && currentData?.captionAuto) {
    showNotice(
      t('content.generate.autoCaptionBlocked'),
      15000,
    );
    return;
  }

  // 이미 전사 중이거나 요청 준비 중(LLM 번역·독음 대기) — 연타를 서버로 내보내지 않는다.
  // 같은 영상의 중복 잡은 임시 오디오 파일을 두고 경합해 다운로드 실패(WinError 32)까지 냈다.
  if (generatingJobs.has(videoId) || preparingGenerate.has(videoId)) return;
  preparingGenerate.add(videoId);
  updateGenChip(); // 버튼을 누르자마자 "준비 중" 칩으로 즉시 반응을 보여준다
  // 걸러낸 줄이 있으면 반드시 알린다 — 조용히 지우면 가사가 사라진 것처럼 보인다
  const removedNote = describeRemoved(cleaned);
  if (removedNote) showNotice(removedNote, 12000);

  try {
    // 자막으로 생성할 때는 가사 텍스트를 보내지 않는다 — video_id만 넘기면 서버가 원어
    // 트랙을 스스로 골라 조달한다(자막 본문은 어차피 서버 yt-dlp로만 받을 수 있다).
    // 번역·독음도 **서버가** 만든다(generate-from-caption이 line_meta를 붙인다): 서버가
    // 정렬에 쓰는 라인 분할은 클라이언트가 본 자막 분할과 다를 수 있고, 병합은 텍스트
    // 매칭이라 여기서 만들어 보내면 아무것도 안 붙는다.

    // 보카로 위키 가사로 생성할 때는 발음/사람 번역도 서버에 함께 저장한다
    // (서버 싱크에 병합돼 다른 프로필·사용자에게도 그대로 표시됨)
    let lineMeta: { text: string; pronunciation?: string; translation?: string }[] | undefined =
      !fromCaption && currentData?.source === 'vocaro'
        ? currentData.lines
          .filter(l => l.pronunciation || l.translation)
          .map(l => ({ text: l.text, pronunciation: l.pronunciation, translation: l.translation }))
        : undefined;

    // 위키 출처는 싱크에 영구 저장돼 조회 시 푸터에 병기된다 (라이선스 표기).
    // currentData.attribution이 있으면(miraheze 등 SourceResult 채택분) 그 값을 그대로
    // 쓴다 — 없는데 source==='vocaro'면 진짜 vocaro 직접 조회다(adoptVocaroResult는
    // attribution을 안 채운다, applyLyricsData의 같은 폴백과 규칙을 맞춘다). 붙여넣기
    // 경로는 사용자가 적어 넣은 출처를 그대로 싣는다 (선택 입력 — 나중에 어디서 온
    // 가사인지 추적할 수 있어 삭제 요청 대응이 쉬워진다)
    const attribution: SourceAttribution | undefined = currentData?.attribution
      ?? (currentData?.source === 'vocaro'
        ? { name: '보카로 가사 위키', url: currentSourceUrl }
        : attributionName?.trim()
          ? { name: attributionName.trim(), url: null }
          : undefined);

    // 위키 발음이 없으면(수동 붙여넣기·LRCLIB 등) LLM 번역·한글 독음을 먼저 받아
    // line_meta로 넘긴다 — 서버가 독음(ko) 정렬 경로를 타고 발음/번역도 싱크에 저장된다.
    // 실패해도 싱크 생성 자체는 계속한다 (원문 정렬 폴백).
    // 3줄 붙여넣기(tri)는 이미 로컬에 발음·번역이 있어 LLM을 부를 필요가 없다
    if (!fromCaption && tri && (!lineMeta || lineMeta.length === 0)) lineMeta = tri;

    // LLM 번역·독음이 필요한 경우(수동 붙여넣기·LRCLIB 등)에는 잡을 **먼저** 만들어
    // 다운로드·보컬 분리를 번역과 겹친다. 직렬로 두면 번역이 끝날 때까지 다운로드조차
    // 시작하지 못한다 — 실측(4.7분 곡)으로 번역 63초가 체감 85초의 74%였다.
    const needsLlmMeta = !fromCaption && (!lineMeta || lineMeta.length === 0);

    const res = fromCaption
      ? await sendToBackground<GenerateResponse>({
        type: 'GENERATE_FROM_CAPTION', payload: { videoId },
      })
      : await sendToBackground<GenerateResponse>({
        type: 'GENERATE_SYNC',
        payload: {
          videoId,
          lyrics: text,
          lineMeta: lineMeta && lineMeta.length > 0 ? lineMeta : undefined,
          lineMetaPending: needsLlmMeta,
          attribution,
          // 제목·아티스트를 싱크에 새겨 둔다 — 나중에 다른 영상이 이 곡의 커버 후보를
          // 찾을 때 서버가 대조할 유일한 단서다 (없으면 후보 탐색이 영원히 빈손)
          title: currentSong?.title,
          artist: currentSong?.artist ?? undefined,
          // 생성 요청자의 번역 언어 — background가 아직 서버 호출에 넘기지 않으면(구버전
          // 배선) 서버 기본값 "ko"로 생성된다(오늘과 동일한 동작)
          targetLang: settings.translationLanguage,
          lineMetaLang: settings.translationLanguage,
        },
      });
    if (res.error || !res.data) {
      const note = failureNote(noteFailure(res.failure));
      if (videoId === currentVideoId && seq === searchSeq) {
        // 요청이 실패했다고 붙여넣던 가사·보던 가사를 버리지 않는다 (reportFailure가 가른다)
        reportFailure(t('content.failure.generateRequest'), note);
      }
      return;
    }
    const jobId = res.data.job_id;
    const alreadyDone = res.data.status === 'completed';
    if (!alreadyDone) {
      // 패널을 점유하지 않는다 — 현재 화면(가사/검색)은 그대로 두고 작은 칩으로 진행률만 표시.
      // 다른 영상으로 이동해도 잡은 계속 추적되고, 완료 후 돌아오면 조회 시 자동 반영된다.
      generatingJobs.set(videoId, { jobId, progress: 0, title: currentSong?.title });
      void persistActiveJobs();
      ensurePolling();
    }

    if (needsLlmMeta) {
      // 서버가 다운로드·보컬 분리를 진행하는 동안 번역·독음을 만든다.
      // 실패해도 **빈 배열로 반드시 한 번 보낸다** — 안 보내면 서버가 정렬 직전에 이 메타를
      // 대기 상한까지 기다려 잡이 헛되게 서 있다(빈 배열 = "붙일 것 없음" 확정 신호).
      let meta: LineMeta[] = [];
      try {
        meta = (await fetchLlmLineMeta(videoId, srcLines)) ?? [];
      } catch {
        meta = []; // 번역 실패 — 서버는 원문 정렬로 폴백한다
      }
      await sendToBackground({
        type: 'ATTACH_LINE_META',
        payload: {
          jobId,
          lineMeta: meta,
          attribution,
          title: currentSong?.title,
          artist: currentSong?.artist ?? undefined,
          // 지연 첨부 번역은 내 언어로 만든 것 — 서버가 그 언어 레이어에 넣는다
          lineMetaLang: settings.translationLanguage,
        },
      });
    }
    // 캐시 히트로 이미 끝난 잡은 위 첨부가 완성된 싱크에 메타를 병합한 뒤 다시 조회한다
    if (alreadyDone && videoId === currentVideoId) void searchLyrics();
  } finally {
    preparingGenerate.delete(videoId);
    updateGenChip();
  }
}

/** 재생성: 현재 everyric 싱크의 가사·발음·출처 그대로 서버 캐시를 무시하고 다시 정렬 */
async function handleRegenerate(): Promise<void> {
  const videoId = currentVideoId;
  const data = currentData;
  if (!videoId || !data?.synced || data.source !== 'everyric') return;
  if (generatingJobs.has(videoId) || preparingGenerate.has(videoId)) return;
  preparingGenerate.add(videoId);
  updateGenChip();

  try {
    const lyrics = data.lines.map(l => l.text).join('\n').trim();
    if (!lyrics) return;
    // 재생성은 **다시 만드는 것**이다 — 저장된 파생물(발음·번역)을 되돌려 넣지 않는다.
    // 재사용해도 되는 것은 yt-dlp로 다시 받을 수 있는 것(오디오·자막)뿐이고, 그건 서버 캐시가
    // 담당한다. 파생물을 되돌려 넣으면 생성 품질이 개선돼도 재생성이 낡은 값을 재생산한다
    // (발음을 결정론적으로 만들게 바꾼 뒤 실제로 그랬다 — 재생성해도 발음이 그대로였다).
    //
    // 사람이 쓴 위키 발음도 '보존'이 아니라 **출처에서 다시 가져온다.** 위키는 언제든 다시
    // 조회할 수 있고, 그동안 위키 쪽이 고쳐졌을 수도 있다.
    const texts = data.lines.map(l => l.text);
    let lineMeta: { text: string; pronunciation?: string; translation?: string }[] = [];
    // lineMeta에 실리는 번역의 언어 — 분기마다 다르다(miraheze=en, vocaro=ko, LLM=내 언어).
    // 서버는 이 값으로 번역을 그 언어의 레이어에 넣고 legacy(ko 전용) 병기 여부를 정한다.
    let lineMetaLang = settings.translationLanguage;
    // 재생성은 everyric 싱크에서만 호출되므로(위 가드) 위키 여부는 출처 표기로만 알 수 있다 —
    // 위키 가사로 만든 싱크는 attribution에 그 이름이 새겨져 내려온다. sourceId가 있으면
    // (miraheze 이후 생성분) 그 값으로, 없으면(구싱크 — attribution에 sourceId가 없던 시절)
    // 이름 문자열 정규식으로 판별한다 — 구데이터 폴백을 OR로 유지한다.
    const fromWiki = Boolean(data.attribution?.sourceId) || /위키/.test(data.attribution?.name ?? '');
    if (fromWiki && currentSong) {
      if (data.attribution?.sourceId === 'miraheze') {
        const wiki = await sendToBackground<SourceResult | null>({
          type: 'MIRAHEZE_LOOKUP', payload: { title: currentSong.title },
        });
        // miraheze 발음은 로마자다 — 서버 독음(ko) 정렬 입력에 로마자를 넣으면 정렬이
        // 붕괴한다(라틴 정렬 실측) — pronunciation은 절대 싣지 않는다, 번역만 넘긴다.
        lineMeta = (wiki.data?.lines ?? [])
          .filter(l => l.translation)
          .map(l => ({ text: l.text, translation: l.translation }));
        lineMetaLang = wiki.data?.translationLang ?? 'en';
      } else {
        const wiki = await sendToBackground<VocaroResult | null>({
          type: 'VOCARO_LOOKUP', payload: { title: currentSong.title },
        });
        lineMeta = (wiki.data?.lines ?? [])
          .filter(l => l.pronunciation || l.translation)
          .map(l => ({ text: l.text, pronunciation: l.pronunciation, translation: l.translation }));
        lineMetaLang = 'ko'; // vocaro 번역은 한국어다
      }
    }
    if (lineMeta.length === 0 && expectsPronunciation(texts)) {
      // 세션 번역 캐시도 비운다 — 안 비우면 이 영상의 낡은 응답이 그대로 다시 실린다
      for (const key of [...translationCache.keys()]) {
        if (key.startsWith(`${videoId}:`)) translationCache.delete(key);
      }
      const fetched = await fetchLlmLineMeta(videoId, texts);
      if (fetched && fetched.length > 0) lineMeta = fetched;
    }

    const res = await sendToBackground<GenerateResponse>({
      type: 'REGENERATE_SYNC',
      payload: {
        videoId,
        lyrics,
        lineMeta: lineMeta.length > 0 ? lineMeta : undefined,
        attribution: data.attribution,
        // 재생성도 제목을 함께 새긴다 — 제목 없이 만들어진 옛 싱크가 이 기회에 채워진다
        title: currentSong?.title,
        artist: currentSong?.artist ?? undefined,
        targetLang: settings.translationLanguage,
        lineMetaLang,
      },
    });
    if (res.error || !res.data) {
      const note = failureNote(noteFailure(res.failure));
      // 재생성 실패는 **기존 싱크가 멀쩡하다는 뜻**이다 — 보고 있던 가사를 지우면 안 된다
      if (videoId === currentVideoId) reportFailure(t('content.failure.regenerateRequest'), note);
      return;
    }
    generatingJobs.set(videoId, { jobId: res.data.job_id, progress: 0, title: currentSong?.title });
    void persistActiveJobs();
    ensurePolling();
  } finally {
    preparingGenerate.delete(videoId);
    updateGenChip();
  }
}

/** 이 영상의 서버 싱크 전부 삭제(초기화) 후 처음부터 다시 검색 — 잘못 붙여넣은 가사 복구용 */
async function handleResetSync(): Promise<void> {
  const videoId = currentVideoId;
  if (!videoId) return;
  const res = await sendToBackground<{ removed_syncs: number }>({
    type: 'SYNC_RESET', payload: { videoId },
  });
  if (res.error) {
    // 초기화가 실패했으면 서버 싱크는 그대로 남아 있다 — 화면의 가사도 그대로 두고 사유만 알린다
    reportFailure(t('content.failure.resetSync'), failureNote(noteFailure(res.failure)));
    return;
  }
  // 지운 것은 **타이밍**이다 — 원문·발음·번역은 남겨 두었다가 재조회가 빈손이면
  // 화면에 되돌린다. 그러면 그 가사로 바로 다시 생성할 수 있다.
  keptLyrics = currentData && currentData.lines.length > 0
    ? { videoId, data: withoutTiming(currentData) }
    : null;
  // 세션 캐시(언어별 번역·발음)와 진행 중 잡 추적도 함께 비워 완전히 처음부터
  for (const key of [...translationCache.keys()]) {
    if (key.startsWith(`${videoId}:`)) translationCache.delete(key);
  }
  removeJob(videoId);
  updateGenChip();
  void searchLyrics();
}

/** 타임싱크를 벗긴 사본 — 초기화 뒤에 남기는 가사는 타이밍이 없는 가사여야 한다.
 *  서버 전사에 딸려 있던 진단·템포·키·링크·품질은 방금 지운 싱크의 것이라 남기면 거짓이 된다. */
function withoutTiming(data: LyricsData): LyricsData {
  return {
    source: data.source,
    synced: false,
    plainText: data.plainText,
    humanTranslated: data.humanTranslated,
    attribution: data.attribution,
    captionAuto: data.captionAuto,
    lines: data.lines.map(l => ({
      time: null,
      endTime: null,
      text: l.text,
      pronunciation: l.pronunciation,
      translation: l.translation,
    })),
  };
}

/** 진행 칩 ✕ — 현재 영상의 전사 잡 취소. 서버는 즉시 또는 다음 단계 경계에서 멈춘다 */
async function handleCancelGenerate(): Promise<void> {
  const videoId = currentVideoId;
  const job = videoId ? generatingJobs.get(videoId) : undefined;
  if (!videoId || !job) return;
  const res = await sendToBackground<{ cancelled: boolean; status?: string }>({
    type: 'JOB_CANCEL', payload: { jobId: job.jobId },
  });
  if (res.error) {
    // 취소가 실패해도 사용자는 계속 그 가사를 보고 있다 — 화면을 갈아치울 근거가 없다
    reportFailure(t('content.failure.cancelRequest'), failureNote(noteFailure(res.failure)));
    return;
  }
  // 그 사이 이미 완료된 잡이면 취소 대신 결과를 반영한다
  if (res.data && !res.data.cancelled && res.data.status === 'completed') {
    removeJob(videoId);
    updateGenChip();
    if (videoId === currentVideoId) void searchLyrics();
    return;
  }
  // 사용자가 직접 취소했으니 실패 알림 없이 추적만 정리
  removeJob(videoId);
  updateGenChip();
}

async function pollJobs(): Promise<void> {
  // 커버 자동 연결 검증 잡도 같은 타이머에 얹혀 돈다 — 둘 다 비어야 타이머를 멈춘다
  if (generatingJobs.size === 0 && linkJobs.size === 0) {
    stopPolling();
    updateGenChip();
    return;
  }
  // 폴링 간격 백오프는 **전사 잡** 응답만 근거로 한다 (아래 for 루프가 도는 경우).
  // 전사 잡이 없는데 anyResponse=false로 읽히면 멀쩡한 서버에서 간격이 늘어난다.
  const hadSyncJobs = generatingJobs.size > 0;
  let anyResponse = false;
  for (const [videoId, job] of [...generatingJobs]) {
    const res = await sendToBackground<JobStatusResponse>({ type: 'JOB_STATUS', payload: { jobId: job.jobId } });
    if (generatingJobs.get(videoId)?.jobId !== job.jobId) continue; // 그 사이 교체/취소됨
    const status = res.data;
    if (!status) continue; // 일시적 실패 — 다음 폴링에서 재시도
    anyResponse = true;

    if (status.status === 'completed') {
      removeJob(videoId);
      const label = job.title ?? videoId;
      if (videoId === currentVideoId) {
        // 결과를 불러온 **뒤에** 알린다 — 잡 성공만 보고 "준비됐어요"라고 하면 발음·번역이
        // 한 줄도 안 붙은 싱크까지 성공으로 보고된다(실측: 자막 경로 0/35줄)
        void searchLyrics().then(() => {
          if (videoId !== currentVideoId) return;
          const verdict = completionVerdict(label);
          notifyJobDone(job.jobId, t('content.notify.transcribeComplete'), verdict.message);
          if (verdict.warning) showNotice(verdict.warning, 20000);
        });
      } else {
        // 다른 영상의 잡은 **검증할 근거가 없다** — currentData는 지금 보는 영상의 것이라
        // 그 싱크에 발음·번역이 붙었는지, 쓸 만한 싱크가 나왔는지 여기서는 알 수 없다.
        // 현재 영상은 completionVerdict로 결과를 보고 말하는데 여기만 무검증으로
        // "준비됐어요"라고 단정하면, 없는 것을 있다고 말하는 절반짜리가 된다.
        // 그래서 사실인 것(잡이 끝났고 싱크가 만들어졌다)만 말하고 확인은 사용자에게 넘긴다.
        notifyJobDone(job.jobId, t('content.notify.transcribeComplete'), t('content.notify.otherVideoReady', [label]));
      }
    } else if (status.status === 'failed') {
      removeJob(videoId);
      // gone = 서버에 잡 기록이 없음(재시작 등) — 무한 폴링 대신 명시적으로 마감
      const errMsg = status.gone
        ? t('content.error.jobGone')
        : (status.error || t('content.error.syncGenerationFailed'));
      notifyJobDone(job.jobId, t('content.notify.transcribeFailed'), `${job.title ?? videoId} — ${errMsg}`);
      if (videoId === currentVideoId) {
        // 전사가 실패해도 지금 보고 있는 가사(자막·LRCLIB·위키·기존 싱크)는 멀쩡하다 —
        // 오류 화면으로 덮으면 실패 문구 하나 때문에 읽던 가사를 잃는다
        reportFailure(errMsg);
      }
    } else {
      job.progress = status.progress ?? job.progress;
      job.stage = status.stage ?? undefined;
      job.stageProgress = status.stage_progress ?? undefined;
      job.queueLabel = status.queue_position != null && status.queue_position > 0
        ? t('content.queue.position', [String(status.queue_position)])
        : (status.status === 'queued' || status.status === 'pending' ? t('content.queue.label') : undefined);
    }
  }
  // 서버가 계속 무응답이면 폴링 간격을 늘려 무의미한 요청을 줄인다 (응답 오면 즉시 복귀)
  if (hadSyncJobs) {
    if (anyResponse) {
      pollFailStreak = 0;
      setPollInterval(POLL_MS_NORMAL);
    } else if (++pollFailStreak >= 5) {
      setPollInterval(POLL_MS_SLOW);
    }
  }
  await pollLinkJobs();
  updateGenChip();
}

/**
 * 전사 완료 판정 — **결과를 실제로 보고** 만든다 (currentData가 이미 갱신된 뒤 호출).
 *
 * 잡 성공은 "쓸 수 있는 싱크"를 뜻하지 않는다. 발음이 기대되는 원문인데 발음·번역이 한 줄도
 * 붙지 않은 싱크가 실제로 만들어졌고(자막 경로), 그걸 "준비됐어요"로 알리니 사용자는 화면을
 * 열어 보고서야 알았다. 무엇이 빠졌는지는 알림과 화면 칩 양쪽에서 말한다.
 */
function completionVerdict(label: string): { message: string; warning: string | null } {
  const data = currentData;
  if (!data?.synced) {
    return { message: t('content.completion.notLoadedMsg', [label]), warning: t('content.completion.notLoadedWarning') };
  }
  // 발음·번역 부재 판정은 CJK 원문에서만 의미가 있다 — 한국어 가사에 독음·번역이
  // 없는 것은 정상이므로 경고하지 않는다
  if (!expectsPronunciation(data.lines.map(l => l.text))) {
    return { message: t('content.completion.readyMsg', [label]), warning: null };
  }
  // `some()`으로 "하나라도 있는가"만 보면 안 된다 — 한 줄만 붙고 나머지가 비어도 "준비됐어요"가
  // 된다. 서버는 응답이 잘린 줄을 failed로 표시해 보내는데(TranslatedLine.failed) 그것도
  // 여태 아무도 읽지 않았다. 부분 실패는 전무와 다른 사실이므로 다르게 말한다.
  const n = data.lines.length;
  const noPron = data.lines.filter(l => !l.pronunciation).length;
  const noTr = data.lines.filter(l => !l.translation).length;
  const missing: string[] = [];
  const partial: string[] = [];
  if (noPron === n) missing.push(t('content.completion.pronWord'));
  else if (noPron > 0) partial.push(t('content.completion.partialPron', [String(n - noPron), String(n)]));
  if (noTr === n) missing.push(t('content.completion.trWord'));
  else if (noTr > 0) partial.push(t('content.completion.partialTr', [String(n - noTr), String(n)]));

  if (missing.length === 0 && partial.length === 0) {
    return { message: t('content.completion.allReadyMsg', [label]), warning: null };
  }
  if (missing.length === 0) {
    const some = partial.join(' · ');
    return {
      message: t('content.completion.partialReadyMsg', [label, some]),
      warning: t('content.completion.partialReadyWarning', [some]),
    };
  }
  const what = missing.join('·');
  const tail = partial.length ? ` (${partial.join(' · ')})` : '';
  return {
    message: t('content.completion.missingMsg', [label, what, tail]),
    warning: t('content.completion.missingWarning', [what, tail]),
  };
}

/** 전사 잡 종료 OS 알림 — 다른 탭/창에 있어도 결과를 알 수 있다.
 *  잡 id를 알림 id로 써서 여러 탭이 같은 잡을 폴링해도 중복되지 않는다. */
function notifyJobDone(jobId: string, title: string, message: string): void {
  if (!settings.notifyOnComplete) return;
  void sendToBackground({ type: 'NOTIFY', payload: { id: `ey-job-${jobId}`, title, message } });
}

/** 진행 칩 갱신 — 현재 영상 잡의 진행률, 그 외 영상 잡은 건수로 요약.
 *  메인 패널과 PiP 양쪽에 같은 문구를 밀어넣는다 (닫혀 있는 쪽은 no-op) — PiP만 보며
 *  '싱크 생성'을 누른 사용자에게 지금까지 진행 표시가 아예 없었다. */
function updateGenChip(): void {
  const cur = currentVideoId ? generatingJobs.get(currentVideoId) : undefined;
  const others = generatingJobs.size - (cur ? 1 : 0);
  let text: string | null = null;
  if (!cur && currentVideoId && preparingGenerate.has(currentVideoId)) {
    // 잡 등록 전 준비 단계 — 버튼이 무반응처럼 보이지 않게 즉시 표시
    text = t('content.genChip.preparing');
  } else if (cur) {
    // 단계명이 오면 "보컬 분리 60% · 전체 68%"처럼 무슨 과정인지 함께 보여준다
    // (cur.stage 자체는 서버가 주는 값이라 여기서 번역하지 않는다 — 서버 i18n은 범위 밖)
    const state = cur.queueLabel
      ?? (cur.stage
        ? t('content.genChip.stageProgress', [cur.stage, String(cur.stageProgress ?? 0), String(cur.progress)])
        : t('content.genChip.percentOnly', [String(cur.progress)]));
    text = t('content.genChip.transcribing', [state, others > 0 ? t('content.genChip.othersSuffix', [String(others)]) : '']);
  } else if (others > 0) {
    text = t('content.genChip.othersOnly', [String(others)]);
  }
  // 칩 클릭 시 펼칠 내 대기열 목록 — 곡명+상태. activeJobs에 이 브라우저가 시킨
  // 잡만 저장되므로 다른 사용자의 큐는 구조적으로 노출되지 않는다.
  const items = [...generatingJobs.entries()]
    .map(([v, j]) => ({
      title: j.title ?? v,
      state: j.queueLabel
        ?? (j.stage ? t('content.genChip.stageOnly', [j.stage, String(j.stageProgress ?? 0)]) : t('content.genChip.percentOnly', [String(j.progress)])),
      isCurrent: v === currentVideoId,
    }))
    .sort((a, b) => Number(b.isCurrent) - Number(a.isCurrent));
  overlay?.setGenerationList(items);
  // 잡이 등록된 뒤에만 취소 가능 (준비 단계는 잡 id가 아직 없다)
  overlay?.setGenerationChip(text, Boolean(cur));
  // PiP에는 대기열 목록·취소 UI가 없다 — 같은 진행 문구만 창 안 칩으로 보여 준다
  pip.setGenerationChip(text);
}

const POLL_MS_NORMAL = 2000;
const POLL_MS_SLOW = 10000;
let pollMs = POLL_MS_NORMAL;
let pollFailStreak = 0;

function ensurePolling(): void {
  if (pollTimer === undefined) {
    pollTimer = window.setInterval(() => void pollJobs(), pollMs);
  }
}

/** 폴링 주기 변경 — 진행 중이면 타이머를 새 주기로 갈아 끼운다 */
function setPollInterval(ms: number): void {
  if (pollMs === ms) return;
  pollMs = ms;
  if (pollTimer !== undefined) {
    clearInterval(pollTimer);
    pollTimer = window.setInterval(() => void pollJobs(), ms);
  }
}

function stopPolling(): void {
  if (pollTimer !== undefined) {
    clearInterval(pollTimer);
    pollTimer = undefined;
  }
}

// ── PiP ────────────────────────────────────────────────────────

async function handlePipToggle(): Promise<void> {
  if (pip.isOpen()) {
    pip.close(); // pagehide → onClosed에서 패널 복원
    return;
  }
  if (!currentData?.synced) return;
  const videoId = currentVideoId;
  const panel = ensureOverlay();
  const opened = await pip.open(cssText, {
    // 메인 가사창과 같은 패널 조각(panels.ts)을 PiP 안에서도 쓴다 — 싱크가 없는 곡에서
    // 창이 닫히는 대신 검색·붙여넣기·생성 UI를 그대로 띄우기 위한 배선
    panel: {
      onGenerate: (lyrics, attribution) => void handleGenerate(lyrics, attribution),
      onRetrySearch: query => void searchLyrics(query),
      onCandidateSearch: query => void handleCandidateSearch(query),
      onPickCandidate: candidate => void handlePickCandidate(candidate),
      onOpenSearch: () => { /* PiP는 자기 창 안에서 연다 (pip.openPanelSearch) */ },
      // 설정 UI는 메인 패널에만 있다 — PiP에서 누르면 유튜브 탭의 패널에 설정을 펼쳐 준다
      onOpenSettings: () => ensureOverlay().openSettings(),
      onRecheckServer: () => void refreshServerStatus(),
      onOpenPermissions: () => void openPermissionsPage(),
    },
    serverStatus,
    theme: resolveTheme(settings), // 판정은 페이지 컨텍스트에서만 가능 — PiP는 받아 쓴다
    debug: settings.debugInfo,
    loadServerLog: () => fetchServerLog(),
    showVideo: settings.pipShowVideo,
    // 저장된 창 크기가 있으면 그대로, 없으면(0) 기존 기본값(440 / 영상 유무에 따라 500·260)
    width: settings.pipWidth > 0 ? settings.pipWidth : 440,
    height: settings.pipHeight > 0 ? settings.pipHeight : (settings.pipShowVideo ? 500 : 260),
    onSizeChange: (w, h) => {
      settings = { ...settings, pipWidth: w, pipHeight: h };
      void saveSettings({ pipWidth: w, pipHeight: h });
    },
    initialVideoRatio: settings.pipVideoRatio,
    showPronunciation: settings.showPronunciation,
    pronScript: resolveScript(settings),
    pitchEnabled: settings.pitchGuide,
    pitchLaneHeight: settings.pitchLaneHeight,
    pitchWindowMeasures: settings.pitchWindowMeasures,
    pitchScrollMode: settings.pitchScrollMode,
    pitchFontScale: settings.pitchFontScale,
    pitchCountdown: settings.pitchCountdown,
    pitchPronPosition: settings.pitchPronPosition,
    showConfidence: settings.debugInfo,
    onPitchHeightChange: px => {
      settings = { ...settings, pitchLaneHeight: px };
      void saveSettings({ pitchLaneHeight: px });
    },
    onSeek: time => engine.seekTo(time),
    onSeekRatio: ratio => {
      const video = engine.getVideo() ?? getVideoElement();
      if (!video || !Number.isFinite(video.duration) || video.duration <= 0) return;
      const sec = ratio * video.duration;
      // 진행바 시크도 되돌림 가드를 받는다 (sync-engine.seekToVideoTime 주석).
      // 엔진이 다른 video에 붙어 있는 창(교체 직후)에는 기존처럼 직접 대입한다.
      if (engine.getVideo() === video) engine.seekToVideoTime(sec);
      else video.currentTime = sec;
    },
    onPlayPause: () => {
      const video = engine.getVideo() ?? getVideoElement();
      if (!video) return;
      if (video.paused) void video.play().catch(() => { /* 사용자 제스처 필요 시 무시 */ });
      else video.pause();
      engine.resync(); // 재생 상태 아이콘 즉시 갱신
    },
    // PiP 창에 포커스가 있을 때의 Alt+Shift+D — 핫키 경로와 같은 함수를 탄다.
    // 패널을 여는 부분까지 그대로 재사용하는 것이 맞다: PiP만 보고 있어도 디버그를 켰으면
    // 메인 패널에서도 보이는 것이 일관적이고, PiP의 디버그 표시는 handleSettingsChange가
    // pip.setDebug로 함께 맞춘다.
    onToggleDebug: () => void toggleDebugInfo(),
    onVolumeChange: volume => {
      const video = engine.getVideo() ?? getVideoElement();
      if (!video) return;
      video.volume = Math.min(1, Math.max(0, volume));
      if (volume > 0 && video.muted) video.muted = false;
    },
    onMuteToggle: () => {
      const video = engine.getVideo() ?? getVideoElement();
      if (video) video.muted = !video.muted;
    },
    onVideoRatioChange: ratio => {
      settings = { ...settings, pipVideoRatio: ratio };
      void saveSettings({ pipVideoRatio: ratio });
    },
    melodyOn: settings.melodyPlayback,
    onMelodyToggle: () => void handleSettingsChange({ melodyPlayback: !settings.melodyPlayback }),
    metronomeOn: settings.metronome,
    onMetronomeToggle: () => void handleSettingsChange({ metronome: !settings.metronome }),
    metronomeRate: settings.metronomeRate,
    onMetronomeRateChange: rate => void handleSettingsChange({ metronomeRate: rate }),
    metronomeBeat: settings.metronomeBeat,
    onMetronomeBeatChange: beat => void handleSettingsChange({ metronomeBeat: beat }),
    micOctave: settings.micOctave,
    onPitchWindowChange: measures => void handleSettingsChange({ pitchWindowMeasures: measures }),
    onPitchScrollModeChange: mode => void handleSettingsChange({ pitchScrollMode: mode }),
    onKaraokeToggle: on => void handleSettingsChange({ pitchGuide: on }),
    onVideoToggle: on => void handleSettingsChange({ pipShowVideo: on }),
    getMicSamples: () => micPitch.samples(),
    onClosed: () => {
      karaokeAudio.setActive(false);
      micPitch.stop();
      overlay?.setPipActive(false);
      // 패널이 placeholder 상태일 때만 복원 (동시 표시 모드면 이미 가사가 떠 있음)
      if (overlay?.isShowingPipPlaceholder()) restoreOverlayState();
    },
  });
  if (!opened) return;
  // requestWindow 대기 중 내비게이션이 일어났으면 stale한 PiP는 닫는다
  if (videoId !== currentVideoId || !currentData?.synced) {
    pip.close();
    return;
  }
  pip.setSong(currentSong?.title ?? '', currentSong?.artist ?? '');
  pip.setTempo(currentData.tempo ?? null);
  pip.setKey(currentData.key ?? null);
  pip.setDebugMeta(currentData.debugMeta ?? null);
  panel.setDebugMeta(currentData.debugMeta ?? null);
  pip.setShowF0(settings.pitchF0Curve);
  pip.setLines(currentData.lines);
  karaokeAudio.setNotes(collectMelodyNotes(currentData.lines));
  karaokeAudio.setTempo(currentData.tempo ?? null);
  karaokeAudio.setActive(true);
  applyAudioSettings();
  if (settings.pipShowVideo) {
    const video = engine.getVideo() ?? getVideoElement();
    if (video) pip.attachVideo(video);
  }
  engine.resync(); // PiP에 현재 라인을 즉시 반영
  panel.setPipActive(true);
  if (!settings.pipKeepPanel) panel.showPipPlaceholder();
}

function restoreOverlayState(): void {
  if (!overlay || currentVideoId === null) return;
  applyLyricsData(currentData);
  updateGenChip(); // 전사 중이면 칩으로 표시 (패널 점유 없음)
}

// ── 서버 상태/유틸 ─────────────────────────────────────────────

/** 마지막으로 확인된 서버 상태(사유 포함) — PiP를 새로 열 때 초기값으로 넘긴다 */
let serverStatus: ServerStatus = unknownStatus();

function applyServerStatus(next: ServerStatus): void {
  const kindChanged = serverStatus.kind !== next.kind;
  serverStatus = next;
  overlay?.setServerStatus(next);
  pip.setServerStatus(next); // PiP도 같은 규칙·같은 배너로 잠근다
  // "가사 없음" 화면은 서버 상태에 따라 문구 자체가 달라진다 — 상태 판정이 검색보다
  // 늦게 도착했으면 PiP 쪽도 다시 그린다 (메인 패널은 setServerStatus가 알아서 한다)
  if (kindChanged && currentData === null && pip.isOpen()) pip.showPanelEmpty(currentSong);
}

async function refreshServerStatus(): Promise<void> {
  const res = await sendToBackground<ServerStatus>({ type: 'SERVER_HEALTH' });
  // 백그라운드 자체와 통신이 끊긴 경우(확장 재설치 등)도 서버를 못 쓰는 상태로 본다
  applyServerStatus(res.data ?? failureToStatus(res.failure));
}

/**
 * 개별 요청이 돌려준 실패 사유를 전역 서버 상태에 반영한다.
 *
 * `/health`는 공개 엔드포인트라 401을 절대 못 본다 — 헬스체크만 믿으면 "서버 정상"인 채로
 * 모든 /api 호출이 401나는 상황이 그대로 재현된다. 그래서 **실제 호출의 실패**도 상태
 * 판정의 근거로 쓴다. 다만 엔드포인트 국소 실패(404 등)로 서버 전체를 죽었다고 하면
 * 멀쩡한 서버에서 버튼이 잠기므로 affectsServerStatus()가 거른다.
 *
 * 회복(→ ok)은 여기서 하지 않는다. 성공한 요청이 Everyric 서버를 거친 것인지
 * (LRCLIB·위키는 서버를 안 탄다) 응답만으로는 알 수 없기 때문이며, 회복 판정은
 * refreshServerStatus()의 명시적 확인에 맡긴다.
 */
function noteFailure(failure: ApiFailure | undefined): ApiFailure | undefined {
  if (failure && affectsServerStatus(failure.kind)) applyServerStatus(failureToStatus(failure));
  return failure;
}

/**
 * 실패를 알린다 — **보고 있던 것을 지우지 않고.**
 *
 * showError는 resetBody()를 타서 화면의 가사·검색 시트·붙여넣던 본문까지 파괴한다. 잡 실패,
 * 생성/재생성 요청 실패, 초기화·취소 실패, 500줄 초과가 전부 그 경로였고, 실패 문구 한 줄을
 * 얻는 대가로 사용자가 옮겨 적은 가사가 사라졌다. 그래서 보존할 것이 있으면(가사·시트·입력)
 * 알림 칩으로만 말하고, 잃을 것이 없는 화면(검색 중·조회 실패 직후)에서만 오류 화면을 띄운다
 * — 그때는 '다시 시도' 버튼까지 함께 줄 수 있어 오류 화면이 더 낫다.
 *
 * 판정은 메인 패널과 PiP가 **각자** 한다: 한쪽은 가사를 띄운 채이고 다른 쪽은 placeholder일
 * 수 있어(pipKeepPanel=false), 한 판정으로 두 창을 몰면 반드시 한쪽이 틀린다.
 */
function reportFailure(message: string, detail?: string): void {
  const full = detail ? `${message} — ${detail}` : message;
  const panel = ensureOverlay();
  if (panel.hasPreservableContent()) panel.setNoticeChip(full, 15000);
  else panel.showError(message, detail);
  if (pip.isOpen()) {
    if (pip.hasPreservableContent()) pip.setNoticeChip(full, 15000);
    else pip.showPanelError(message, detail);
  }
}

/**
 * 한 줄 알림 — 메인 패널과 PiP 양쪽에 같은 소식을 띄운다 (닫혀 있는 쪽은 no-op).
 *
 * 지금까지 알림은 메인 패널만 갔다. pipKeepPanel=false로 PiP만 보고 있으면 거절 사유·
 * 완료 경고·표기 필터 결과를 볼 기회가 아예 없어, PiP에서 '싱크 생성'을 누르면 무반응이었다.
 * 지우는 호출(null)로는 패널을 새로 만들지 않는다 — 없던 패널이 알림 없이 튀어나오면 안 된다.
 */
function showNotice(text: string | null, autoHideMs?: number): void {
  if (text) ensureOverlay().setNoticeChip(text, autoHideMs);
  else overlay?.setNoticeChip(null);
  pip.setNoticeChip(text, autoHideMs);
}

/**
 * 서버 오류 detail의 관용적(tolerant) 파싱 — 계획 Task 13 잔여.
 *
 * 오늘 everyric-api.ts의 readErrorDetail은 detail이 문자열이 아니면 JSON.stringify해
 * 문자열로 넘긴다 — 그래서 서버가 `{code, message}` 객체로 바꿔도(다음 릴리스 예정) 지금은
 * 그 JSON을 문자열째로 받는다. 여기서 다시 파싱을 시도해 code가 있으면 카탈로그
 * (`serverError.<code>`)에서 로컬라이즈를 찾고, 없으면 message를, 그마저 없으면 원문을
 * 그대로 쓴다 — 평범한 문자열(오늘 거의 모든 경우)은 JSON.parse가 실패해 그대로 통과한다.
 * 서버가 실제로 객체를 보내기 시작해도(everyric-api.ts가 그때 맞춰 바뀌면) 이 함수는
 * 손대지 않아도 된다.
 */
function tolerantDetailText(raw: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return raw;
  }
  if (!parsed || typeof parsed !== 'object') return raw;
  const obj = parsed as { code?: string; message?: string };
  if (obj.code) {
    const key = `serverError.${obj.code}`;
    const localized = t(key);
    if (localized !== key) return localized; // 카탈로그에 없으면 t()가 키 자체를 돌려준다
  }
  return obj.message ?? raw;
}

/** 실패 사유를 화면 문구 뒤에 붙일 한 줄로 — 없으면 undefined */
function failureNote(failure: ApiFailure | undefined): string | undefined {
  if (!failure) return undefined;
  const status = failureToStatus(failure);
  const detail = status.detail ? tolerantDetailText(status.detail) : undefined;
  return detail ? `${statusLine(status)} — ${detail}` : statusLine(status);
}

async function sendToBackground<T>(message: BgRequest): Promise<MessageResponse<T>> {
  try {
    return await chrome.runtime.sendMessage(message) as MessageResponse<T>;
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) };
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => void init());
} else {
  void init();
}
