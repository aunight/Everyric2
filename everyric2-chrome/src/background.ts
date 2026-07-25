import { fetchFromLrclib, getLrclibById, searchTracksLrclib } from './lib/lrclib';
import { attachLineMeta, cancelJob, checkServerStatus, fetchCaptionLines, findLinkCandidates, generateSync, generateSyncFromCaption, getJobStatus, getLinkJobStatus, getServerLog, linkSync, listSyncs, lookupSync, regenerateSync, resetSync, saveUserOffset, translateLyrics, unlinkSync, vocaroMatch, type FailureSink, type ServerConfig } from './lib/everyric-api';
import { parseLRC, parsePlainLyrics, segmentsToLines } from './lib/lyrics-parser';
import { fetchSongPage, vocaroLookup } from './lib/vocaro';
import { getSettings } from './lib/settings';
import type { BgRequest, LRCLibTrack, LyricsData, MessageResponse, SearchCandidate, SongInfo } from './types';

async function getServerConfig(): Promise<ServerConfig> {
  const { serverUrl, apiKey } = await getSettings();
  return { serverUrl, apiKey };
}

/**
 * 서버 호출 한 건을 응답으로 감싼다 — 실패하면 코드**와 사유**를 함께 실어 보낸다.
 *
 * 예전에는 `res ? { data: res } : { error: '...' }`였다. 그래서 콘텐츠 쪽은 실패한 건
 * 알아도 "왜"는 영영 알 수 없었다. sink를 여기서 만들어 넘기므로 호출부마다
 * 보일러플레이트가 늘지 않는다.
 */
async function call<T>(
  errorCode: string, fn: (sink: FailureSink) => Promise<T | null>,
): Promise<MessageResponse<T>> {
  const sink: FailureSink = {};
  const data = await fn(sink);
  return data === null ? { error: errorCode, failure: sink.failure } : { data };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  handleMessage(message as BgRequest)
    .then(sendResponse)
    .catch((error: unknown) => {
      sendResponse({ error: error instanceof Error ? error.message : String(error) });
    });
  return true;
});

// everyric.com 웹사이트에서 싱크 생성 완료 알림 (기존 플로우 유지)
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  const origin = sender.origin ?? '';
  const isEveryric = origin === 'https://everyric.com' || origin.endsWith('.everyric.com');
  const videoId = (message?.payload as { videoId?: unknown } | undefined)?.videoId;
  if (isEveryric && message?.type === 'SYNC_COMPLETE' && typeof videoId === 'string') {
    void broadcastToYouTubeTabs({ videoId });
    sendResponse({ success: true });
  }
  return true;
});

// 툴바 아이콘 클릭 → 해당 탭의 오버레이 토글
chrome.action.onClicked.addListener(tab => {
  if (tab.id !== undefined) {
    chrome.tabs.sendMessage(tab.id, { type: 'TOGGLE_OVERLAY' }).catch(() => {
      /* content script가 없는 탭(비 YouTube)은 무시 */
    });
  }
});

/**
 * 핫키(Alt+Shift+D) → 해당 탭의 디버그 정보 표시 토글.
 *
 * 키 선택 근거: 유튜브가 알파벳 단일키를 거의 다 쓴다(k j l f t i m c o w, 0-9, , . < > /,
 * Space, 화살표). 크롬은 Ctrl+Shift+{I J C M T W B O N **D** A}를 예약하므로 Ctrl+Shift+D는
 * 쓸 수 없다(모든 탭 북마크). Alt+Shift 조합은 유튜브·크롬·OS 어디에도 겹치지 않고, 패널은
 * 키 이벤트를 stopPropagation으로 끊으므로(overlay.ts) 패널 안 타이핑과도 충돌하지 않는다.
 *
 * macOS에서는 Alt가 Option으로 매핑돼 Option+Shift+D가 특수문자 입력과 겹칠 수 있다.
 * commands가 먼저 가로채므로 실동작에는 문제가 없을 것으로 보지만 **실측하지 않았다** —
 * 문제가 있으면 사용자가 chrome://extensions/shortcuts에서 재지정할 수 있다.
 */
chrome.commands.onCommand.addListener((command, tab) => {
  if (command !== 'toggle-debug' || tab?.id === undefined) return;
  chrome.tabs.sendMessage(tab.id, { type: 'TOGGLE_DEBUG' }).catch(() => {
    /* content script가 없는 탭(비 YouTube)은 무시 */
  });
});

async function handleMessage(message: BgRequest): Promise<MessageResponse> {
  switch (message.type) {
    case 'FETCH_LYRICS': {
      // 여기서 null은 "가사가 없다"일 수도, "서버가 못 줬다"일 수도 있다 — 그 둘을
      // 화면이 구분할 수 있도록 서버 조회 실패 사유를 data와 함께 실어 보낸다
      const sink: FailureSink = {};
      const data = await fetchLyricsChain(message.payload, message.payload.skipLrclib === true, sink);
      return { data, failure: sink.failure };
    }

    case 'FETCH_LRCLIB': {
      const track = await fetchFromLrclib(message.payload);
      return { data: track ? lrclibToLyricsData(track) : null };
    }

    case 'SEARCH_CANDIDATES':
      return { data: await searchCandidates(message.payload) };

    case 'PICK_LRCLIB': {
      const track = await getLrclibById(message.payload.id);
      return { data: track ? lrclibToLyricsData(track) : null };
    }

    case 'GENERATE_SYNC': {
      const server = await getServerConfig();
      return call('generate_request_failed', sink => generateSync(server, {
        video_id: message.payload.videoId,
        lyrics: message.payload.lyrics,
        language: message.payload.language,
        line_meta: message.payload.lineMeta,
        line_meta_pending: message.payload.lineMetaPending,
        attribution: message.payload.attribution,
        title: message.payload.title,
        artist: message.payload.artist,
      }, sink));
    }
    case 'ATTACH_LINE_META': {
      const server = await getServerConfig();
      return call('attach_line_meta_failed', sink => attachLineMeta(server, message.payload.jobId, {
        line_meta: message.payload.lineMeta,
        attribution: message.payload.attribution,
        title: message.payload.title,
        artist: message.payload.artist,
      }, sink));
    }

    case 'REGENERATE_SYNC': {
      const server = await getServerConfig();
      return call('regenerate_request_failed', sink => regenerateSync(server, {
        video_id: message.payload.videoId,
        lyrics: message.payload.lyrics,
        line_meta: message.payload.lineMeta,
        attribution: message.payload.attribution,
        title: message.payload.title,
        artist: message.payload.artist,
      }, sink));
    }

    case 'JOB_STATUS': {
      const server = await getServerConfig();
      return call('job_status_failed', sink => getJobStatus(server, message.payload.jobId, sink));
    }

    case 'JOB_CANCEL': {
      const server = await getServerConfig();
      return call('job_cancel_failed', sink => cancelJob(server, message.payload.jobId, sink));
    }

    case 'NOTIFY': {
      // 전사 잡 종료를 다른 탭/창에 있어도 알 수 있게 OS 알림으로. 같은 id로 만들면
      // 여러 탭이 같은 잡을 폴링해도 알림이 중복되지 않고 갱신된다.
      try {
        chrome.notifications.create(message.payload.id ?? `ey-${Date.now()}`, {
          type: 'basic',
          iconUrl: chrome.runtime.getURL('icons/icon128.png'),
          title: message.payload.title,
          message: message.payload.message,
        });
      } catch { /* 알림 실패는 치명적이지 않다 */ }
      return { data: true };
    }

    case 'TRANSLATE': {
      const server = await getServerConfig();
      return call('translate_failed', sink => translateLyrics(
        server, message.payload.text, message.payload.targetLang,
        { title: message.payload.title, artist: message.payload.artist }, sink,
      ));
    }

    case 'SERVER_HEALTH': {
      return { data: await checkServerStatus(await getServerConfig()) };
    }

    // 최근 서버 요청 몇 건 — 패널·PiP의 접이식 로그가 펼쳐질 때만 가져간다.
    // 경로·본문의 키류는 everyric-api가 기록 시점에 이미 마스킹했다.
    case 'SERVER_LOG':
      return { data: getServerLog() };

    case 'VOCARO_LOOKUP': {
      const direct = await vocaroLookup(message.payload.title);
      if (direct) return { data: direct };
      // 일본어 원제는 클라이언트의 한국어 독음 인덱스로 못 찾는다 — 서버 원제 인덱스 폴백
      const matched = await vocaroMatch(await getServerConfig(), message.payload.title);
      return { data: matched?.found && matched.slug ? await fetchSongPage(matched.slug) : null };
    }

    case 'SYNC_LINK': {
      const server = await getServerConfig();
      return call('link_failed', sink => linkSync(server, {
        video_id: message.payload.videoId,
        source_video_id: message.payload.sourceVideoId,
        offset_sec: message.payload.offsetSec,
        rate: message.payload.rate,
      }, sink));
    }

    // 커버 자동 연결: 같은 곡 후보 탐색 (서버가 검증 잡까지 자동 제출한다).
    // 이 엔드포인트가 없는 구버전 서버는 404 → error가 되고, content는 조용히 포기한다.
    case 'LINK_CANDIDATES': {
      const server = await getServerConfig();
      return call('link_candidates_unavailable', sink => findLinkCandidates(server, message.payload.videoId, {
        title: message.payload.title,
        artist: message.payload.artist,
      }, sink));
    }

    case 'LINK_JOB_STATUS': {
      const server = await getServerConfig();
      return call('link_job_status_failed', sink =>
        getLinkJobStatus(server, message.payload.linkJobId, sink));
    }

    case 'SYNC_UNLINK': {
      const server = await getServerConfig();
      return call('unlink_failed', sink => unlinkSync(server, message.payload.videoId, sink));
    }

    case 'SYNC_RESET': {
      const server = await getServerConfig();
      return call('sync_reset_failed', sink => resetSync(server, message.payload.videoId, sink));
    }

    case 'SYNC_OFFSET': {
      const server = await getServerConfig();
      return call('sync_offset_failed', sink =>
        saveUserOffset(server, message.payload.videoId, message.payload.offsetSec, sink));
    }

    case 'SYNC_LIST': {
      // 검색 필터가 생겨 후보를 넉넉히 받는다 — 서버 목록은 최신순.
      // 빈 배열([])과 "서버가 못 줬다"를 화면이 구분할 수 있게 실패 사유를 함께 보낸다
      const sink: FailureSink = {};
      const res = await listSyncs(await getServerConfig(), 200, sink);
      return { data: res ?? [], failure: sink.failure };
    }

    case 'VOCARO_PAGE':
      return { data: await fetchSongPage(message.payload.slug) };

    // 자막 **본문**은 서버(yt-dlp) 경유 — 워치 페이지에서 긁은 timedtext URL은
    // POT(proof-of-origin) 강제로 브라우저 플레이어 밖에선 빈 응답이 온다 (실측).
    // 트랙 **목록**은 content가 워치 페이지에서 직접 읽으므로 서버를 부르지 않는다.
    case 'YT_CAPTION_TEXT': {
      const server = await getServerConfig();
      const res = await call('caption_text_failed', sink => fetchCaptionLines(
        server, message.payload.videoId, message.payload.lang, message.payload.auto, sink,
      ));
      return res.data ? { data: res.data.lines } : { error: res.error, failure: res.failure };
    }

    // 자막을 보고 있다가 누른 '싱크 생성' — 서버가 자막을 직접 읽는 전용 경로.
    // 아직 배포되지 않은 서버면 null이 오고, content가 기존 생성 경로로 폴백한다.
    case 'GENERATE_FROM_CAPTION': {
      const server = await getServerConfig();
      return call('generate_from_caption_unavailable', sink =>
        generateSyncFromCaption(server, message.payload.videoId, sink));
    }

    default:
      return { error: 'unknown_message_type' };
  }
}

// E2E 스모크 테스트가 SW 컨텍스트에서 직접 호출하기 위한 노출 — 프로덕션 동작에는 영향 없음
(globalThis as { __vocaroLookup?: typeof vocaroLookup }).__vocaroLookup = vocaroLookup;

/** 우선순위: Everyric 서버(단어 타이밍 보존) → (skipLrclib가 아니면) LRCLIB 싱크 → LRCLIB 일반 */
async function fetchLyricsChain(
  song: SongInfo, skipLrclib = false, sink?: FailureSink,
): Promise<LyricsData | null> {
  // 제목·아티스트를 함께 보낸다 — 서버가 기존 싱크의 빈 제목을 이 기회에 채운다(백필).
  // 재생성 없이 코퍼스에 제목이 쌓이는 유일한 경로이고, 그게 없으면 커버 링크 후보
  // 탐색이 영원히 빈손이다.
  const sync = await lookupSync(
    await getServerConfig(), song.videoId, { title: song.title, artist: song.artist }, sink,
  );
  // 서버에 저장된 영상별 사용자 오프셋 — 싱크가 없어도(found=false) 내려온다
  const userOffset = sync?.user_offset ?? undefined;
  if (sync?.found && sync.timestamps && sync.timestamps.length > 0) {
    const lines = segmentsToLines(sync.timestamps);
    if (lines.length > 0) {
      return {
        source: 'everyric',
        synced: true,
        lines,
        plainText: lines.map(l => l.text).join('\n'),
        // 서버가 사람 번역(위키 병합분)을 내려줬으면 기계번역으로 덮어쓰지 않는다
        humanTranslated: lines.some(l => l.translation),
        debugMeta: sync.debug ?? undefined,
        attribution: sync.attribution ?? undefined,
        tempo: sync.tempo ?? undefined,
        key: sync.key ?? undefined,
        qualityScore: sync.quality_score ?? undefined,
        createdAt: sync.created_at ?? undefined,
        linked: sync.linked
          ? {
              sourceVideoId: sync.linked.source_video_id,
              offsetSec: sync.linked.offset_sec,
              rate: sync.linked.rate ?? undefined,
              // 서버가 안 내려주는 구버전이면 undefined → 화면은 '검증 여부 모름'으로 표시
              verified: sync.linked.verified ?? undefined,
            }
          : undefined,
        userOffset,
      };
    }
  }

  // 보카로 위키 우선 설정이면 LRCLIB은 content 쪽에서 위키 미스 이후에 별도로 시도한다
  if (skipLrclib) return null;
  const track = await fetchFromLrclib(song);
  const data = track ? lrclibToLyricsData(track) : null;
  if (data) data.userOffset = userOffset;
  return data;
}

function lrclibToLyricsData(track: LRCLibTrack): LyricsData | null {
  if (track.syncedLyrics) {
    const lines = parseLRC(track.syncedLyrics);
    if (lines.length > 0) {
      return {
        source: 'lrclib',
        synced: true,
        lines,
        plainText: track.plainLyrics ?? lines.map(l => l.text).join('\n'),
      };
    }
  }
  if (track.plainLyrics) {
    const lines = parsePlainLyrics(track.plainLyrics);
    if (lines.length > 0) {
      return { source: 'lrclib', synced: false, lines, plainText: track.plainLyrics };
    }
  }
  return null;
}

/** 수동 검색 후보: LRCLIB 트랙들 + 보카로 위키 매칭(서버 원제 인덱스 → 클라 독음 인덱스) */
async function searchCandidates(query: { title: string; artist: string; duration: number }): Promise<SearchCandidate[]> {
  const [tracks, wikiMatch] = await Promise.all([
    searchTracksLrclib(query),
    vocaroMatch(await getServerConfig(), query.title),
  ]);

  const candidates: SearchCandidate[] = [];
  if (wikiMatch?.found && wikiMatch.slug) {
    candidates.push({
      source: 'vocaro',
      slug: wikiMatch.slug,
      title: wikiMatch.ja ?? wikiMatch.ko ?? query.title,
      url: wikiMatch.page_url ?? `http://vocaro.wikidot.com/${wikiMatch.slug}`,
    });
  } else {
    // 서버가 없거나 미스 — 클라이언트 독음 인덱스로 한 번 더 (페이지까지 확보되면 그 제목 사용)
    const direct = await vocaroLookup(query.title);
    if (direct) {
      candidates.push({ source: 'vocaro', slug: direct.slug, title: direct.pageTitle, url: direct.pageUrl });
    }
  }
  for (const t of tracks) {
    candidates.push({
      source: 'lrclib',
      id: t.id,
      title: t.trackName ?? '(제목 없음)',
      artist: t.artistName ?? '',
      duration: t.duration ?? 0,
      synced: Boolean(t.syncedLyrics),
    });
  }
  return candidates;
}

async function broadcastToYouTubeTabs(payload: { videoId: string }): Promise<void> {
  const tabs = await chrome.tabs.query({
    url: ['*://www.youtube.com/*', '*://music.youtube.com/*'],
  });
  for (const tab of tabs) {
    if (tab.id !== undefined) {
      chrome.tabs.sendMessage(tab.id, { type: 'SYNC_GENERATED', payload }).catch(() => {
        /* content script 미주입 탭은 무시 */
      });
    }
  }
}
