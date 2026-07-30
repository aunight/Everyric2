/** 網易雲音樂歌詞來源。
 *
 * LRC 原文與 tlyric 人工翻譯會在 netease-lyrics.ts 集中處理：
 * 中文原文及中文翻譯轉為繁體，日文原文保持來源字形。
 *
 * 非官方 API 可能無預警變更或受地區限制；失敗回傳 null，讓其他來源繼續嘗試。
 */

import { Converter } from 'opencc-js';

import type { SongInfo } from '../types';
import { primaryArtistForSearch } from './artist-name.ts';

const SEARCH_URL = 'https://music.163.com/api/search/get/web';
const LYRIC_URL = 'https://music.163.com/api/song/lyric';

export interface NeteaseTrack {
  id: number;
  title: string;
  artist: string;
  /** 초 단위 (API는 ms) */
  duration: number;
}

async function getJSON<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) return null;
    return await res.json() as T;
  } catch {
    return null;
  }
}

interface RawSong {
  id: number;
  name: string;
  artists?: { name: string }[];
  duration?: number;
}

export async function searchNetease(
  query: { title: string; artist: string }, limit = 6,
): Promise<NeteaseTrack[]> {
  const artist = primaryArtistForSearch(query.artist);
  const s = artist ? `${artist} ${query.title}` : query.title;
  const params = new URLSearchParams({ s, type: '1', limit: String(limit), offset: '0' });
  const json = await getJSON<{ result?: { songs?: RawSong[] } }>(`${SEARCH_URL}?${params}`);
  const songs = json?.result?.songs ?? [];
  return songs.map(song => ({
    id: song.id,
    title: song.name,
    artist: (song.artists ?? []).map(a => a.name).join(', '),
    duration: Math.round((song.duration ?? 0) / 1000),
  }));
}

export interface NeteaseLyric {
  /** LRC 원문 가사 (타임태그 포함) — 없으면 null */
  lrc: string | null;
  /** LRC 중국어 번역 — 원문과 같은 타임태그를 공유한다. 없으면 null */
  tlyric: string | null;
}

export async function fetchNeteaseLyric(id: number): Promise<NeteaseLyric | null> {
  const params = new URLSearchParams({ id: String(id), lv: '1', tv: '-1' });
  const json = await getJSON<{ lrc?: { lyric?: string }; tlyric?: { lyric?: string } }>(
    `${LYRIC_URL}?${params}`,
  );
  if (!json) return null;
  const lrc = json.lrc?.lyric?.trim() || null;
  const tlyric = json.tlyric?.lyric?.trim() || null;
  if (!lrc) return null;
  return { lrc, tlyric };
}

const toTraditional = Converter({ from: 'cn', to: 'tw' });

function normalizeMatchText(text: string): string {
  return toTraditional(text).toLowerCase().normalize('NFKC').replace(/[^\p{L}\p{N}]+/gu, '');
}

function textMatches(candidate: string, expected: string): boolean {
  const a = normalizeMatchText(candidate);
  const b = normalizeMatchText(expected);
  if (a.length < 2 || b.length < 2) return false;
  return a.includes(b) || b.includes(a);
}

/**
 * 자동 조회는 검색 첫 항목을 그대로 쓰지 않는다. 같은 제목의 다른 곡이 흔하므로 제목을
 * 반드시 확인하고, 아티스트·영상 길이가 맞는 후보를 우선한다.
 */
export function pickBestNeteaseTrack(
  tracks: NeteaseTrack[],
  song: Pick<SongInfo, 'title' | 'artist' | 'duration'>,
): NeteaseTrack | null {
  const expectedTitle = normalizeMatchText(song.title);
  const expectedArtist = primaryArtistForSearch(song.artist);
  return tracks
    .filter(track => textMatches(track.title, song.title))
    .map(track => {
      const titleExact = normalizeMatchText(track.title) === expectedTitle;
      const artistMatches = !expectedArtist || textMatches(track.artist, expectedArtist);
      const durationDiff = song.duration > 0 && track.duration > 0
        ? Math.abs(track.duration - song.duration)
        : 120;
      let score = durationDiff + (titleExact ? 0 : 25);
      if (!artistMatches) score += 300;
      if (durationDiff > 20) score += 500;
      return { track, score };
    })
    .sort((a, b) => a.score - b.score)[0]?.track ?? null;
}

export async function fetchFromNetease(
  song: Pick<SongInfo, 'title' | 'artist' | 'duration'>,
): Promise<{ track: NeteaseTrack; lyric: NeteaseLyric } | null> {
  const tracks = await searchNetease({ title: song.title, artist: song.artist ?? '' }, 10);
  const track = pickBestNeteaseTrack(tracks, song);
  if (!track) return null;
  const lyric = await fetchNeteaseLyric(track.id);
  return lyric ? { track, lyric } : null;
}
