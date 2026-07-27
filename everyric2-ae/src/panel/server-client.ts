import { normalizeSyncPayload } from "./planner";
import type { SyncDocument } from "./types";

/** 서버가 받는 영상 id (everyric2/server/api/sync.py의 _VIDEO_ID_PATTERN과 같다). */
const VIDEO_ID_PATTERN = /^[A-Za-z0-9_-]{11}$/;

const REQUEST_TIMEOUT_MS = 10_000;

export interface ServerSyncResult {
  document: SyncDocument;
  attribution?: { name?: string; url?: string };
  linked?: { source_video_id?: string; offset_sec?: number; verified?: boolean };
  qualityScore?: number;
}

interface SyncLookupResponse {
  found: boolean;
  timestamps?: unknown[];
  language?: string;
  quality_score?: number;
  attribution?: { name?: string; url?: string };
  linked?: { source_video_id?: string; offset_sec?: number; verified?: boolean };
}

/**
 * 유튜브 주소나 id에서 영상 id를 뽑는다. 알아볼 수 없으면 null.
 *
 * watch?v=, youtu.be/, shorts/, embed/, live/ 형태와 11자 id를 그대로 받는다.
 */
export function extractVideoId(input: string): string | null {
  const trimmed = input.trim();
  if (!trimmed) return null;
  if (VIDEO_ID_PATTERN.test(trimmed)) return trimmed;

  const patterns = [
    /[?&]v=([A-Za-z0-9_-]{11})/,
    /youtu\.be\/([A-Za-z0-9_-]{11})/,
    /\/shorts\/([A-Za-z0-9_-]{11})/,
    /\/embed\/([A-Za-z0-9_-]{11})/,
    /\/live\/([A-Za-z0-9_-]{11})/,
  ];
  for (const pattern of patterns) {
    const match = trimmed.match(pattern);
    if (match?.[1]) return match[1];
  }
  return null;
}

/** 끝의 슬래시를 떼고, 스킴이 없으면 https를 붙인다. */
export function normalizeServerUrl(url: string): string {
  const trimmed = url.trim().replace(/\/+$/, "");
  if (!trimmed) return "";
  if (/^https?:\/\//.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

/**
 * 서버에 이미 있는 싱크를 가져온다.
 *
 * 응답의 timestamps는 세그먼트 배열이고 각 항목에 words(글자 타이밍)·pronunciation·
 * translation이 들어 있다 — planner의 normalizeSyncPayload가 그대로 읽는 형태다.
 */
export async function fetchServerSync(
  serverUrl: string,
  videoIdOrUrl: string,
  apiKey?: string,
): Promise<ServerSyncResult> {
  const base = normalizeServerUrl(serverUrl);
  if (!base) throw new Error("서버 주소가 비어 있습니다.");
  const videoId = extractVideoId(videoIdOrUrl);
  if (!videoId) throw new Error("유튜브 주소에서 영상 ID를 찾지 못했습니다.");

  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (apiKey) headers["X-API-Key"] = apiKey;
    const response = await fetch(`${base}/api/sync/${videoId}`, {
      headers,
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`서버 응답 HTTP ${response.status}`);
    const payload = (await response.json()) as SyncLookupResponse;
    if (!payload.found || !payload.timestamps || payload.timestamps.length === 0) {
      throw new Error("이 영상의 싱크가 서버에 없습니다.");
    }
    const document = normalizeSyncPayload(
      { timestamps: payload.timestamps, language: payload.language },
      `서버 · ${videoId}`,
    );
    return {
      document,
      ...(payload.attribution ? { attribution: payload.attribution } : {}),
      ...(payload.linked ? { linked: payload.linked } : {}),
      ...(typeof payload.quality_score === "number" ? { qualityScore: payload.quality_score } : {}),
    };
  } finally {
    window.clearTimeout(timer);
  }
}
