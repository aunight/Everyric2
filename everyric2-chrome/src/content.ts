import { detectSong, getCurrentVideoId, getVideoElement } from './lib/song-detector';
import { SyncEngine, type SyncHandlers } from './lib/sync-engine';
import { KaraokeAudio, collectMelodyNotes } from './lib/karaoke-audio';
import { parseTriLineLyrics } from './lib/tri-line';
import { describeRemoved, stripPartMarkers } from './lib/lyrics-clean';
import { MicPitch } from './lib/mic-pitch';
import { getGeometry, getSettings, saveGeometry, saveSettings } from './lib/settings';
import { LyricsOverlay } from './ui/overlay';
import { PipController } from './ui/pip';
import {
  captionSourceLabel,
  getCaptionTracks,
  mergeCaptionTranslation,
  selectKoreanTrack,
  selectLyricTrack,
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
  SyncListItem,
  TranslateResult,
  TranslatedLine,
} from './types';
import { affectsServerStatus, failureToStatus, serverKnownBad, statusLine, unknownStatus } from './lib/server-status';
import { resolveTheme } from './lib/theme';
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
const LINK_PROBE_CHIP = '동일 곡 추정 — 자동 연결 확인 중…';
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
  } else if (message.type === 'SYNC_GENERATED' && message.payload.videoId === currentVideoId) {
    void searchLyrics();
  }
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
    // 미러 스트림도 새 video 기준으로 갱신
    if (pip.isOpen() && settings.pipShowVideo) pip.attachVideo(video);
  }
}

/** 자동 검색이 꺼져 있으면 사용자가 패널을 열어둔 경우에만 따라간다.
 * 자동 검색이 켜져 있어도 음악 영상으로 판별될 때만 자동으로 뜬다 —
 * 브이로그/게임 영상에서 노래를 찾겠다고 패널이 뜨는 것을 막는다. */
function shouldFollow(): boolean {
  if (overlay?.isVisible()) return true; // 사용자가 열어둔 패널은 항상 따라간다
  if (!settings.autoSearch) return false;
  // 쇼츠는 기본적으로 자동으로 열지 않는다 (설정으로 허용 가능, 수동 열기는 그대로)
  if (!settings.autoSearchShorts && location.pathname.startsWith('/shorts/')) return false;
  return isLikelyMusicVideo();
}

/** 음악 영상 판별 — 유튜브 자체 신호 우선, 없으면 채널/제목 휴리스틱 */
function isLikelyMusicVideo(): boolean {
  // 1) 설명란 '음악' 섹션 (콘텐츠 ID로 곡이 식별된 영상) — 가장 신뢰
  if (document.querySelector('ytd-video-description-music-section-renderer')) return true;
  // 2) 워치 페이지 microdata 장르 — 있으면 그대로 믿는다 (Music이 아니면 차단)
  const genre = document.querySelector<HTMLMetaElement>('meta[itemprop="genre"]');
  if (genre?.content) {
    const g = genre.content.trim().toLowerCase();
    return g === 'music' || g === '음악';
  }
  // 3) 자동 생성 음악 채널(" - Topic")
  const channel = document.querySelector('ytd-watch-metadata ytd-channel-name a')?.textContent?.trim() ?? '';
  if (/ - Topic$/.test(channel)) return true;
  // 4) 제목 휴리스틱 — MV/가사/커버/보컬로이드 계열 표기
  const title = document.title;
  return /(M\/?V|Official\s*(Music\s*)?Video|뮤직\s*비디오|가사|lyrics?|\bcover(ed)?\b|커버|불러보았다|歌ってみた|feat\.|ft\.|【[^】]*(MV|PV|오리지널|Original)[^】]*】)/i.test(title);
}

function checkCurrentPage(): void {
  const videoId = getCurrentVideoId();
  if (!videoId) {
    cleanupForPage();
    return;
  }
  if (videoId === currentVideoId || !shouldFollow()) return;
  currentVideoId = videoId;
  void searchLyrics();
}

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
  overlay?.setNoticeChip(null);
  noticeVideoId = null;
  // 전사 잡은 서버에서 계속 돌므로 추적을 유지한다 (완료 시 해당 영상으로 돌아오면 반영)
  engine.stop();
  pip.close();
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
    currentVideoId = videoId;
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

  // 한국어 자막이 따로 있으면 시간 겹침으로 붙여 2단 표시 (수동작성만 — 자동생성은
  // ASR 오차가 그대로 남아 가사 번역으로 쓰기엔 품질이 떨어진다)
  let translations: (string | undefined)[] = [];
  const ko = selectKoreanTrack(tracks, track);
  if (ko) {
    const krLines = await fetchCaptionLines(videoId, ko.lang, ko.auto);
    if (videoId !== currentVideoId) return null;
    if (krLines.length > 0) translations = mergeCaptionTranslation(base, krLines);
  }

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
    ensureOverlay().setLinkStatus('자기 자신에게는 연결할 수 없어요');
    return;
  }
  // 자체 전사가 있으면 조회가 링크보다 자체 전사를 우선해 연결이 무시된다 —
  // 사용자가 명시적으로 연결을 원했으니 확인 후 자체 전사를 지우고 연결한다
  if (currentData?.synced && currentData.source === 'everyric' && !currentData.linked) {
    const ok = window.confirm(
      '이 영상에는 자체 전사가 이미 있어요.\n연결하면 자체 전사를 삭제하고 원본 영상의 싱크를 대신 사용합니다. 계속할까요?',
    );
    if (!ok) {
      ensureOverlay().setLinkStatus('연결 취소됨 — 자체 전사를 유지합니다');
      return;
    }
    const reset = await sendToBackground<{ removed_syncs: number }>({
      type: 'SYNC_RESET', payload: { videoId },
    });
    if (reset.error) {
      const note = failureNote(noteFailure(reset.failure));
      ensureOverlay().setLinkStatus(`자체 전사 삭제에 실패했어요${note ? ` — ${note}` : ''}`);
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
      ? `연결 실패 — ${note}`
      : '연결 실패 — 원본 영상에 전사(싱크)가 있는지 확인해 주세요');
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
    ensureOverlay().setLinkStatus(`해제 실패${note ? ` — ${note}` : ' — 서버 상태를 확인해 주세요'}`);
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
    if (videoId === currentVideoId) overlay?.setNoticeChip(LINK_PROBE_CHIP);
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
      if (videoId === currentVideoId) overlay?.setNoticeChip(null);
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
      overlay?.setNoticeChip(null); // 미매치·실패는 조용히 원래 상태로
      continue;
    }
    const conf = status.confidence != null ? ` (반주 일치 ${Math.round(status.confidence * 100)}%)` : '';
    overlay?.setNoticeChip(`자동 연결됨 — 같은 곡의 싱크를 가져왔어요${conf}`, 12000);
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
  overlay?.applySettings(settings);
  // 키를 고치는 것이 인증 실패의 정상 복구 경로다 — URL과 함께 즉시 재확인한다
  if (patch.serverUrl !== undefined || patch.apiKey !== undefined) void refreshServerStatus();
  if (patch.debugInfo !== undefined) pip.setDebug(patch.debugInfo);
  // 메인 패널은 위 applySettings에서 이미 바뀐다 — PiP도 같은 판정값으로 함께 맞춘다
  if (patch.theme !== undefined) pip.setTheme(resolveTheme(settings));

  if (patch.showTranslation === true || (patch.translationLanguage && settings.showTranslation)) {
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
  const t = time - videoOffset;
  if (meta.star_spans?.some(([s, e]) => t >= s && t < e)) return '추임새★';
  if (meta.vad_regions == null) return null;
  return meta.vad_regions.some(([s, e]) => t >= s && t < e) ? '가창' : '간주·무성';
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
  });
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
    if (v.pronunciation && !line.pronunciation) line.pronunciation = v.pronunciation;
    if (v.translation && !line.translation) {
      line.translation = v.translation;
      data.humanTranslated = true;
    }
  }
}

function clearTranslations(): void {
  overlay?.setTranslationStatus(null);
  if (!currentData) return;
  if (currentData.source === 'vocaro' || currentData.humanTranslated) return; // 사람 번역은 가사 자체의 일부 — 지우지 않는다
  for (const line of currentData.lines) delete line.translation;
  overlay?.refreshTranslations();
  pip.refresh();
}

async function loadTranslations(): Promise<void> {
  const data = currentData;
  const videoId = currentVideoId;
  if (!data || !videoId || !settings.showTranslation) return;
  if (data.source === 'vocaro' || data.humanTranslated) return; // 위키가 이미 사람 번역을 제공
  // 서버 싱크에 번역·발음이 이미 저장돼 있으면(생성 시 LLM 메타 병합) LLM 재호출 생략.
  // 단, 발음이 기대되는 원문(일본어 등 CJK)인데 발음이 하나도 없으면 — 번역만 저장된
  // 낡은 싱크 — 발음까지 다시 받아온다 (그냥 반환하면 발음이 영영 채워지지 않는다)
  const expectsPron = expectsPronunciation(data.lines.map(l => l.text));
  if (
    data.lines.every(l => l.translation)
    && (data.lines.some(l => l.pronunciation) || !expectsPron)
  ) return;

  const lang = settings.translationLanguage;
  const cached = translationCacheGet(`${videoId}:${lang}`);
  // 캐시도 같은 기준으로 검증 — 발음 빠진 캐시(구버전 응답)는 다시 받아온다
  if (cached && (!expectsPron || cached.some(l => l.pronunciation))) {
    applyTranslations(data, cached);
    return;
  }

  // 번역은 서버 전용이다 — 고장난 걸 알면서 "생성 중…"을 띄우는 건 작동하는 척하는 것
  if (serverKnownBad(serverStatus)) {
    overlay?.setTranslationStatus(`번역 불가 — ${statusLine(serverStatus)}`);
    return;
  }
  overlay?.setTranslationStatus('번역·발음 생성 중…');
  const lines = await requestTranslation(videoId, data.lines.map(l => l.text));
  if (currentData !== data || currentVideoId !== videoId) return; // 곡이 바뀜
  if (!settings.showTranslation || settings.translationLanguage !== lang) return;

  if (!lines || lines.length === 0) {
    // requestTranslation이 실패 사유를 이미 상태에 반영했다 — 그 사유를 그대로 보여 준다
    overlay?.setTranslationStatus(serverKnownBad(serverStatus)
      ? `번역 실패 — ${statusLine(serverStatus)}`
      : '번역 실패 — 서버가 결과를 주지 않았어요');
    return;
  }
  applyTranslations(data, lines);
}

function applyTranslations(data: LyricsData, translated: TranslatedLine[]): void {
  let pronApplied = false;
  data.lines.forEach((line, i) => {
    const t = translated[i]?.translation?.trim();
    // '[NO API KEY]'는 구버전 서버의 키 미설정 플레이스홀더 — 번역으로 표시하지 않는다
    if (t && t !== line.text && !t.startsWith('[NO API KEY]')) line.translation = t;
    // 발음표기(target=ko면 한글 독음) — 사람이 단 발음(보카로 위키)이 있으면 건드리지 않는다
    const p = translated[i]?.pronunciation?.trim();
    if (p && !line.pronunciation) {
      line.pronunciation = p;
      pronApplied = true;
    }
  });
  overlay?.setTranslationStatus(null);
  overlay?.refreshTranslations();
  // 발음이 새로 붙었으면 PiP 내부 변환 캐시(setLines 시점 복사)도 다시 채운다
  if (pronApplied && currentData === data) pip.setLines(data.lines);
  pip.refresh();
}

/** 원문에 CJK(가나·한자 등)가 실질적으로 있으면 발음표기(한글 독음)가 기대되는 곡 */
function expectsPronunciation(texts: string[]): boolean {
  const cjk = texts.join('').match(/[぀-ヿ㐀-鿿]/g);
  return (cjk?.length ?? 0) >= 5;
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
  const key = `${videoId}:${settings.translationLanguage}`;
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
    overlay?.setTranslationStatus('AI 번역·독음 생성 중…');
    let translated = translationCacheGet(`${videoId}:${lang}`);
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
    panel.setNoticeChip(linkJobs.has(videoId) ? LINK_PROBE_CHIP : null);
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

  // 소스 우선순위: 서버 싱크는 항상 최우선, 그 다음은 설정에 따라
  // 보카로 위키(발음·사람 번역) → LRCLIB 순서 또는 그 반대
  const wikiFirst = settings.lyricsSourcePriority === 'vocaro';
  const res = await sendToBackground<LyricsData | null>({
    type: 'FETCH_LYRICS',
    payload: { ...song, skipLrclib: wikiFirst },
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
    panel.showError('가사를 불러오지 못했어요', note);
    if (pip.isOpen()) pip.showPanelError('가사를 불러오지 못했어요', note);
    return;
  }

  let data = res.data ?? null;
  currentSourceUrl = null;
  if (!data) {
    const vocaro = await sendToBackground<VocaroResult | null>({
      type: 'VOCARO_LOOKUP',
      payload: { title: song.title },
    });
    if (seq !== searchSeq || videoId !== currentVideoId) return;
    if (vocaro.data && vocaro.data.lines.length > 0) {
      data = adoptVocaroResult(videoId, vocaro.data);
    }
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

/** 수동 검색: 소스별 후보 리스트를 모아 패널에 전달 */
async function handleCandidateSearch(query: { title: string; artist: string }): Promise<void> {
  const videoId = currentVideoId;
  if (!videoId) return;
  const res = await sendToBackground<SearchCandidate[]>({
    type: 'SEARCH_CANDIDATES',
    payload: { ...query, duration: currentSong?.duration ?? 0 },
  });
  if (videoId !== currentVideoId) return;
  ensureOverlay().showSearchResults(res.data ?? []);
}

/** 후보 선택: 해당 소스에서 가사를 받아 현재 가사를 교체한다 (잘못 가져온 가사 롤백 경로) */
async function handlePickCandidate(candidate: SearchCandidate): Promise<void> {
  const videoId = currentVideoId;
  if (!videoId) return;
  const seq = ++searchSeq; // 진행 중이던 자동 검색/생성 흐름은 폐기
  removeJob(videoId); // 다른 가사를 고르면 이 영상의 기존 전사 추적은 버린다
  updateGenChip();
  engine.stop();
  const panel = ensureOverlay();
  panel.showLoading('선택한 가사를 불러오는 중…');

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
    panel.showError('선택한 가사를 불러오지 못했어요');
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

  if (!data) {
    // 싱크가 없다고 PiP를 닫지 않는다 — 재생목록을 돌리다 가사 없는 곡이 나오면
    // 창이 증발해 매번 브라우저 창으로 돌아가야 했다. 같은 패널 조각을 PiP 안에
    // 띄워 거기서 바로 검색·붙여넣기·생성 요청을 할 수 있게 한다.
    if (pip.isOpen()) pip.showPanelEmpty(currentSong);
    panel.showEmpty(currentSong);
    return;
  }
  if (data.synced) {
    if (pip.isOpen()) {
      pip.setTempo(data.tempo ?? null);
      pip.setKey(data.key ?? null);
      pip.setDebugMeta(data.debugMeta ?? null);
      pip.setShowF0(settings.pitchF0Curve);
      pip.setLines(data.lines);
      karaokeAudio.setNotes(collectMelodyNotes(data.lines));
      karaokeAudio.setTempo(data.tempo ?? null);
      if (settings.pipKeepPanel) {
        panel.showSyncedLyrics(data.lines, data.source, data.plainText);
        panel.setPipEnabled(PipController.isSupported());
      } else {
        panel.showPipPlaceholder();
      }
      panel.setPipActive(true);
    } else {
      panel.showSyncedLyrics(data.lines, data.source, data.plainText);
      panel.setPipEnabled(PipController.isSupported());
    }
    void startEngine(data.lines);
  } else {
    // 싱크 없는 플레인 가사도 PiP를 유지한 채 창 안에 보여준다
    if (pip.isOpen()) pip.showPanelPlain(data.lines, data.plainText);
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
  if (pip.isOpen() && settings.pipShowVideo) pip.attachVideo(video);
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
    ensureOverlay().showError(`가사가 너무 길어요 (${srcLines.length}줄) — 500줄 이하로 줄여 주세요`);
    return;
  }
  const text = srcLines.join('\n');

  // 이미 전사 중이거나 요청 준비 중(LLM 번역·독음 대기) — 연타를 서버로 내보내지 않는다.
  // 같은 영상의 중복 잡은 임시 오디오 파일을 두고 경합해 다운로드 실패(WinError 32)까지 냈다.
  if (generatingJobs.has(videoId) || preparingGenerate.has(videoId)) return;
  preparingGenerate.add(videoId);
  updateGenChip(); // 버튼을 누르자마자 "준비 중" 칩으로 즉시 반응을 보여준다
  // 걸러낸 줄이 있으면 반드시 알린다 — 조용히 지우면 가사가 사라진 것처럼 보인다
  const removedNote = describeRemoved(cleaned);
  if (removedNote) ensureOverlay().setNoticeChip(removedNote, 12000);

  try {
    // 유튜브 자막을 보다가 생성을 누른 경우엔 가사 텍스트를 보내지 않는다 — video_id만
    // 넘기면 서버가 원어 트랙을 스스로 골라 조달한다(자막 본문은 어차피 서버 yt-dlp로만
    // 받을 수 있다). 번역·독음도 서버가 만들므로 여기서 LLM을 부를 필요가 없다.
    const fromCaption = currentData?.source === 'caption';

    // 보카로 위키 가사로 생성할 때는 발음/사람 번역도 서버에 함께 저장한다
    // (서버 싱크에 병합돼 다른 프로필·사용자에게도 그대로 표시됨)
    let lineMeta: { text: string; pronunciation?: string; translation?: string }[] | undefined =
      !fromCaption && currentData?.source === 'vocaro'
        ? currentData.lines
          .filter(l => l.pronunciation || l.translation)
          .map(l => ({ text: l.text, pronunciation: l.pronunciation, translation: l.translation }))
        : undefined;

    // 위키 출처는 싱크에 영구 저장돼 조회 시 푸터에 병기된다 (CC BY 표기).
    // 붙여넣기 경로는 사용자가 적어 넣은 출처를 그대로 싣는다 (선택 입력 — 나중에
    // 어디서 온 가사인지 추적할 수 있어 삭제 요청 대응이 쉬워진다)
    const attribution = currentData?.source === 'vocaro'
      ? { name: '보카로 가사 위키', url: currentSourceUrl }
      : attributionName?.trim()
        ? { name: attributionName.trim(), url: null }
        : undefined;

    // 위키 발음이 없으면(수동 붙여넣기·LRCLIB 등) LLM 번역·한글 독음을 먼저 받아
    // line_meta로 넘긴다 — 서버가 독음(ko) 정렬 경로를 타고 발음/번역도 싱크에 저장된다.
    // 실패해도 싱크 생성 자체는 계속한다 (원문 정렬 폴백).
    // 3줄 붙여넣기(tri)는 이미 로컬에 발음·번역이 있어 LLM을 부를 필요가 없다
    if (!fromCaption && tri && (!lineMeta || lineMeta.length === 0)) lineMeta = tri;

    // LLM 번역·독음이 필요한 경우(수동 붙여넣기·LRCLIB 등)에는 잡을 **먼저** 만들어
    // 다운로드·보컬 분리를 번역과 겹친다. 직렬로 두면 번역이 끝날 때까지 다운로드조차
    // 시작하지 못한다 — 실측(4.7분 곡)으로 번역 63초가 체감 85초의 74%였다.
    const needsLlmMeta = !fromCaption && (!lineMeta || lineMeta.length === 0);

    const panel = ensureOverlay();
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
        },
      });
    if (res.error || !res.data) {
      const note = failureNote(noteFailure(res.failure));
      if (videoId === currentVideoId && seq === searchSeq) {
        panel.showError('싱크 생성 요청에 실패했어요.', note);
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
    let lineMeta: { text: string; pronunciation?: string; translation?: string }[] = data.lines
      .filter(l => l.pronunciation || l.translation)
      .map(l => ({ text: l.text, pronunciation: l.pronunciation, translation: l.translation }));

    // 발음이 기대되는 원문인데 발음이 하나도 없으면(번역만 저장된 낡은 싱크) LLM 독음을
    // 새로 받아 재생성이 독음 정렬 경로를 타게 한다 — 안 그러면 발음 없는 싱크가 재생산된다
    const texts = data.lines.map(l => l.text);
    if (!data.lines.some(l => l.pronunciation) && expectsPronunciation(texts)) {
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
      },
    });
    if (res.error || !res.data) {
      const note = failureNote(noteFailure(res.failure));
      if (videoId === currentVideoId) ensureOverlay().showError('재생성 요청에 실패했어요.', note);
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
    ensureOverlay().showError('싱크 초기화에 실패했어요.', failureNote(noteFailure(res.failure)));
    return;
  }
  // 세션 캐시(언어별 번역·발음)와 진행 중 잡 추적도 함께 비워 완전히 처음부터
  for (const key of [...translationCache.keys()]) {
    if (key.startsWith(`${videoId}:`)) translationCache.delete(key);
  }
  removeJob(videoId);
  updateGenChip();
  void searchLyrics();
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
    ensureOverlay().showError('취소 요청에 실패했어요.', failureNote(noteFailure(res.failure)));
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
      notifyJobDone(job.jobId, '전사 완료', `${job.title ?? videoId} — 가사 싱크가 준비됐어요`);
      if (videoId === currentVideoId) void searchLyrics();
    } else if (status.status === 'failed') {
      removeJob(videoId);
      // gone = 서버에 잡 기록이 없음(재시작 등) — 무한 폴링 대신 명시적으로 마감
      const errMsg = status.gone
        ? '서버에서 작업 기록을 찾을 수 없어요 (서버 재시작 등) — 다시 생성해 주세요'
        : (status.error || '싱크 생성에 실패했어요');
      notifyJobDone(job.jobId, '전사 실패', `${job.title ?? videoId} — ${errMsg}`);
      if (videoId === currentVideoId) {
        ensureOverlay().showError(errMsg);
      }
    } else {
      job.progress = status.progress ?? job.progress;
      job.stage = status.stage ?? undefined;
      job.stageProgress = status.stage_progress ?? undefined;
      job.queueLabel = status.queue_position != null && status.queue_position > 0
        ? `대기열 ${status.queue_position}번째`
        : (status.status === 'queued' || status.status === 'pending' ? '대기열' : undefined);
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

/** 전사 잡 종료 OS 알림 — 다른 탭/창에 있어도 결과를 알 수 있다.
 *  잡 id를 알림 id로 써서 여러 탭이 같은 잡을 폴링해도 중복되지 않는다. */
function notifyJobDone(jobId: string, title: string, message: string): void {
  if (!settings.notifyOnComplete) return;
  void sendToBackground({ type: 'NOTIFY', payload: { id: `ey-job-${jobId}`, title, message } });
}

/** 진행 칩 갱신 — 현재 영상 잡의 진행률, 그 외 영상 잡은 건수로 요약 */
function updateGenChip(): void {
  if (!overlay) return;
  const cur = currentVideoId ? generatingJobs.get(currentVideoId) : undefined;
  const others = generatingJobs.size - (cur ? 1 : 0);
  let text: string | null = null;
  if (!cur && currentVideoId && preparingGenerate.has(currentVideoId)) {
    // 잡 등록 전 준비 단계 — 버튼이 무반응처럼 보이지 않게 즉시 표시
    text = '싱크 생성 준비 중 — AI 번역·독음 요청…';
  } else if (cur) {
    // 단계명이 오면 "보컬 분리 60% · 전체 68%"처럼 무슨 과정인지 함께 보여준다
    const state = cur.queueLabel
      ?? (cur.stage
        ? `${cur.stage} ${cur.stageProgress ?? 0}% · 전체 ${cur.progress}%`
        : `${cur.progress}%`);
    text = `전사 중 ${state}${others > 0 ? ` · 외 ${others}건` : ''}`;
  } else if (others > 0) {
    text = `다른 영상 전사 중 ${others}건`;
  }
  // 칩 클릭 시 펼칠 내 대기열 목록 — 곡명+상태. activeJobs에 이 브라우저가 시킨
  // 잡만 저장되므로 다른 사용자의 큐는 구조적으로 노출되지 않는다.
  const items = [...generatingJobs.entries()]
    .map(([v, j]) => ({
      title: j.title ?? v,
      state: j.queueLabel
        ?? (j.stage ? `${j.stage} ${j.stageProgress ?? 0}%` : `${j.progress}%`),
      isCurrent: v === currentVideoId,
    }))
    .sort((a, b) => Number(b.isCurrent) - Number(a.isCurrent));
  overlay.setGenerationList(items);
  // 잡이 등록된 뒤에만 취소 가능 (준비 단계는 잡 id가 아직 없다)
  overlay.setGenerationChip(text, Boolean(cur));
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
      if (video && Number.isFinite(video.duration) && video.duration > 0) {
        video.currentTime = ratio * video.duration;
      }
    },
    onPlayPause: () => {
      const video = engine.getVideo() ?? getVideoElement();
      if (!video) return;
      if (video.paused) void video.play().catch(() => { /* 사용자 제스처 필요 시 무시 */ });
      else video.pause();
      engine.resync(); // 재생 상태 아이콘 즉시 갱신
    },
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

/** 실패 사유를 화면 문구 뒤에 붙일 한 줄로 — 없으면 undefined */
function failureNote(failure: ApiFailure | undefined): string | undefined {
  if (!failure) return undefined;
  const status = failureToStatus(failure);
  return status.detail ? `${statusLine(status)} — ${status.detail}` : statusLine(status);
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
