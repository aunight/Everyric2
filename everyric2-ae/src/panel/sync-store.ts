import fs from "fs";
import os from "os";
import path from "path";

import type { SyncDocument } from "./types";

/**
 * 컴포지션마다 싱크 데이터를 디스크에 매어 둔다.
 *
 * 패널은 CEP 창이 닫히면 메모리를 통째로 잃는다. 싱크를 다시 불러오는 것은 파일을 고르거나
 * 서버를 부르거나 곡을 다시 정렬하는 일이라, 창을 여닫을 때마다 반복할 수 없다.
 *
 * localStorage가 아니라 파일에 두는 이유: 곡 하나의 싱크가 글자 단위 타이밍까지 담아
 * 수백 KB에 이르고, 여러 곡을 오가면 localStorage 한도를 넘긴다.
 */

const STORE_VERSION = 1;
const MAX_ENTRIES = 60;
const MAX_BYTES = 40 * 1024 * 1024;

interface StoredSync {
  version: number;
  savedAt: number;
  compName?: string;
  document: SyncDocument;
}

function storeRoot(): string {
  const base = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
  return path.join(base, "Everyric", "sync-cache");
}

/** 파일 이름으로 쓸 수 있는 짧은 지문. crypto에 기대지 않으려고 FNV-1a를 직접 돌린다. */
function fingerprint(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(36);
}

/**
 * 저장 열쇠. 프로젝트를 아직 저장하지 않았으면 경로가 비는데, 그때는 컴포지션 id만 쓴다 —
 * AE를 다시 켜면 id가 흩어지지만 그 프로젝트는 어차피 파일이 없어 되찾을 근거가 없다.
 */
export function compKey(projectPath: string | undefined, compId: number | undefined): string | null {
  if (!compId) return null;
  return `${fingerprint(`${(projectPath ?? "").toLowerCase()}::${compId}`)}-${compId}`;
}

function entryPath(key: string): string {
  return path.join(storeRoot(), `${key}.json`);
}

export function saveSyncForComp(
  projectPath: string | undefined,
  compId: number | undefined,
  document: SyncDocument,
  compName?: string,
): void {
  const key = compKey(projectPath, compId);
  if (!key) return;
  try {
    fs.mkdirSync(storeRoot(), { recursive: true });
    const payload: StoredSync = {
      version: STORE_VERSION,
      savedAt: Date.now(),
      ...(compName ? { compName } : {}),
      document,
    };
    fs.writeFileSync(entryPath(key), JSON.stringify(payload), "utf8");
    pruneStore();
  } catch {
    // 저장 실패는 작업을 막을 이유가 아니다 — 이번 세션 동안은 메모리에 그대로 있다.
  }
}

export function loadSyncForComp(
  projectPath: string | undefined,
  compId: number | undefined,
): SyncDocument | null {
  const key = compKey(projectPath, compId);
  if (!key) return null;
  try {
    const file = entryPath(key);
    if (!fs.existsSync(file)) return null;
    const parsed = JSON.parse(fs.readFileSync(file, "utf8")) as StoredSync;
    if (parsed.version !== STORE_VERSION) return null;
    const document = parsed.document;
    if (!document || !Array.isArray(document.lines) || document.lines.length === 0) return null;
    return document;
  } catch {
    return null;
  }
}

export function forgetSyncForComp(projectPath: string | undefined, compId: number | undefined): void {
  const key = compKey(projectPath, compId);
  if (!key) return;
  try {
    fs.rmSync(entryPath(key), { force: true });
  } catch {
    // 지우지 못해도 다음 저장이 덮어쓴다.
  }
}

/** 오래된 것부터 지워 캐시가 무한히 자라지 않게 한다. */
function pruneStore(): void {
  try {
    const root = storeRoot();
    const entries = fs
      .readdirSync(root)
      .filter((name) => name.endsWith(".json"))
      .map((name) => {
        const full = path.join(root, name);
        const stat = fs.statSync(full);
        return { full, mtime: stat.mtimeMs, size: stat.size };
      })
      .sort((a, b) => b.mtime - a.mtime);

    let bytes = 0;
    entries.forEach((entry, index) => {
      bytes += entry.size;
      if (index >= MAX_ENTRIES || bytes > MAX_BYTES) {
        try {
          fs.rmSync(entry.full, { force: true });
        } catch {
          // 다음 기회에 지운다.
        }
      }
    });
  } catch {
    // 캐시 정리는 부수적인 일이다.
  }
}
