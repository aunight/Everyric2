import type { SongInfo } from '../types';
import { artistForDisplay } from './artist-name.ts';
import { parseSongTitle } from './song-title';

export function getCurrentVideoId(): string | null {
  try {
    const url = new URL(location.href);
    if (url.pathname === '/watch') return url.searchParams.get('v');
    // Shorts·임베드·라이브 경로는 videoId가 경로에 실려 온다
    const m = url.pathname.match(/^\/(?:shorts|embed|live)\/([A-Za-z0-9_-]{11})(?:[/?]|$)/);
    if (m) return m[1];
  } catch {
    /* URL 파싱 실패는 videoId 없음으로 처리 */
  }
  return null;
}

export function getVideoElement(): HTMLVideoElement | null {
  const videos = Array.from(document.querySelectorAll<HTMLVideoElement>('video'));
  if (videos.length === 0) return null;
  // 페이지에 프리뷰/광고 등 여러 video가 있을 수 있으므로 실제 재생 중인 것을 우선하되,
  // 그중에서도 본편 플레이어(html5-main-video)를 먼저, 인라인 프리뷰·광고 슬롯 안의
  // video는 후순위로 — 홈 피드 프리뷰가 재생 중이면 가사가 엉뚱한 영상에 붙는다
  const playing = videos.filter(v => !v.paused && v.readyState >= 2 && v.currentTime > 0);
  const mainPlaying = playing.find(v => v.classList.contains('html5-main-video'));
  if (mainPlaying) return mainPlaying;
  const nonPreview = playing.find(
    v => !v.closest('ytd-video-preview, ytd-ad-slot-renderer, ytd-in-feed-ad-layout-renderer'),
  );
  if (nonPreview) return nonPreview;
  if (playing.length > 0) return playing[0];
  return videos.find(v => v.classList.contains('html5-main-video')) ?? videos[0];
}

function textOf(selector: string): string {
  return document.querySelector(selector)?.textContent?.trim() ?? '';
}

function displayArtist(value: string | null | undefined): string | null {
  return artistForDisplay(value) || null;
}

export function detectSong(): SongInfo | null {
  const videoId = getCurrentVideoId();
  if (!videoId) return null;

  const rawDuration = getVideoElement()?.duration ?? 0;
  const duration = Number.isFinite(rawDuration) ? Math.round(rawDuration) : 0;

  const meta = navigator.mediaSession?.metadata;
  if (meta?.title) {
    // mediaSession 제목도 "가수 - 곡명" 통짜인 경우가 흔하다(뮤비 업로드 관례) — DOM
    // 경로와 같은 규칙으로 쪼갠다. 쪼개지면 그 가수 표기가 채널명(meta.artist)보다 정확하다.
    const split = parseSongTitle(meta.title);
    return {
      title: split.title,
      artist: displayArtist(split.artist ?? meta.artist),
      videoId,
      duration,
    };
  }

  if (location.host === 'music.youtube.com') {
    const title = textOf('ytmusic-player-bar .title');
    if (title) {
      const byline = textOf('ytmusic-player-bar .byline');
      const artist = byline.split('•')[0]?.trim() || null;
      const parsed = parseSongTitle(title);
      return {
        title: parsed.title,
        artist: displayArtist(parsed.artist ?? artist),
        videoId,
        duration,
      };
    }
  }

  const rawTitle = textOf('h1.ytd-watch-metadata yt-formatted-string')
    || textOf('#title h1')
    || document.title.replace(/ - YouTube$/, '').trim();
  if (!rawTitle || rawTitle === 'YouTube') return null;

  const channel = textOf('#owner #channel-name a') || null;
  const split = parseSongTitle(rawTitle);
  return {
    title: split.title,
    artist: displayArtist(split.artist ?? channel),
    videoId,
    duration,
  };
}
