import { resolvedPronunciation } from "./lang";
import type { CutPiece, CutPoint, CutReveal, CharTiming, CutSession, MatchQuality, PronScript, SyncDocument, SyncLine, TextLayerInfo, TimingAtom } from "./types";

/** 컷끼리, 그리고 컷과 레이어 경계 사이에 최소한 남겨 두는 간격(초). 30fps 한 프레임. */
const MIN_PIECE_SEC = 1 / 30;

function isWhitespace(char: string): boolean {
  return /\s/.test(char);
}

/** 공백을 지운 비교용 키. 레이어 텍스트는 줄바꿈·들여쓰기가 라인과 다를 수 있다. */
function comparisonKey(text: string): string {
  return text.replace(/\s+/g, "");
}

/**
 * atom 하나를 글자 단위로 편다. 글자 단위 CTC면 이미 한 글자라 그대로,
 * 단어 단위 atom("hello")이면 글자 수로 균등 분할한다.
 */
function explodeAtom(atom: TimingAtom): CharTiming[] {
  const chars = Array.from(atom.text).filter((char) => !isWhitespace(char));
  if (chars.length === 0) return [];
  const span = Math.max(0, atom.end - atom.start);
  const step = span / chars.length;
  return chars.map((char, index) => ({
    char,
    start: atom.start + step * index,
    end: atom.start + step * (index + 1),
    visible: true,
    // 한 atom을 쪼갠 값은 측정치가 아니라 추정치다 — UI가 구분해서 보여준다.
    interpolated: chars.length > 1,
  }));
}

/**
 * 레이어 텍스트의 각 글자에 시각을 배정한다.
 *
 * 가시 글자는 atom을 편 큐에서 순서대로 가져가고, 공백은 이웃 사이를 메운다.
 * 큐가 모자라면 남은 글자를 마지막 시각과 fallbackEnd 사이에 균등 배분한다.
 */
function assignCharTimings(
  text: string,
  atoms: TimingAtom[],
  fallbackStart: number,
  fallbackEnd: number,
): CharTiming[] {
  const characters = Array.from(text);
  if (characters.length === 0) return [];

  const queue = atoms.flatMap(explodeAtom);
  const visibleCount = characters.filter((char) => !isWhitespace(char)).length;
  const result: CharTiming[] = [];

  // 큐가 모자랄 때 쓸 균등 배분 구간: 큐가 끝나는 지점부터 fallbackEnd까지.
  const shortfall = Math.max(0, visibleCount - queue.length);
  const tailStart = queue.length > 0 ? (queue[queue.length - 1]?.end ?? fallbackStart) : fallbackStart;
  const tailStep = shortfall > 0 ? Math.max(0, fallbackEnd - tailStart) / shortfall : 0;

  let taken = 0;
  let tailTaken = 0;
  for (const char of characters) {
    if (isWhitespace(char)) {
      // 공백은 폭이 없는 경계로 둔다. 앞뒤가 정해진 뒤 아래에서 채운다.
      result.push({ char, start: 0, end: 0, visible: false, interpolated: true });
      continue;
    }
    const fromQueue = queue[taken];
    if (fromQueue) {
      result.push({ ...fromQueue, char });
      taken += 1;
      continue;
    }
    const start = tailStart + tailStep * tailTaken;
    tailTaken += 1;
    result.push({
      char,
      start,
      end: tailStart + tailStep * tailTaken,
      visible: true,
      interpolated: true,
    });
  }

  // 공백의 시각을 이웃에서 메운다: 앞 가시 글자의 end ~ 뒤 가시 글자의 start.
  for (let index = 0; index < result.length; index += 1) {
    const entry = result[index];
    if (!entry || entry.visible) continue;
    let before: CharTiming | undefined;
    for (let back = index - 1; back >= 0; back -= 1) {
      const candidate = result[back];
      if (candidate?.visible) {
        before = candidate;
        break;
      }
    }
    let after: CharTiming | undefined;
    for (let forward = index + 1; forward < result.length; forward += 1) {
      const candidate = result[forward];
      if (candidate?.visible) {
        after = candidate;
        break;
      }
    }
    entry.start = before?.end ?? after?.start ?? fallbackStart;
    entry.end = after?.start ?? before?.end ?? fallbackEnd;
    if (entry.end < entry.start) entry.end = entry.start;
  }

  return result;
}

interface LineMatch {
  line: SyncLine;
  quality: Exclude<MatchQuality, "none">;
  atoms: TimingAtom[];
}

/** 라인의 atoms 중 가시 글자 [from, to) 구간에 해당하는 것만 잘라 온다. */
function sliceAtomsByVisibleRange(line: SyncLine, from: number, to: number): TimingAtom[] {
  const exploded = line.atoms.flatMap(explodeAtom);
  if (exploded.length === 0) return [];
  const window = exploded.slice(from, to);
  if (window.length === 0) return [];
  return window.map((entry) => ({ text: entry.char, start: entry.start, end: entry.end }));
}

/**
 * 후보 중 레이어 구간과 가장 잘 겹치는 것을 고른다.
 *
 * **후렴은 반복된다.** 텍스트만 보고 처음 만난 라인을 잡으면 2절 레이어가 1절의 시각을
 * 가져와 조각이 곡 앞머리로 날아간다(실측: 22.68초 레이어가 1.79초 컷을 받았다).
 * 겹침이 없으면 음수가 되어 자연히 순위가 밀리므로, 가장 가까운 것이 남는다.
 */
function pickByOverlap<T extends { line: SyncLine }>(candidates: T[], layer: TextLayerInfo): T {
  let best = candidates[0] as T;
  let bestScore = -Infinity;
  for (const candidate of candidates) {
    const overlap =
      Math.min(candidate.line.end, layer.outPoint) - Math.max(candidate.line.start, layer.inPoint);
    if (overlap > bestScore) {
      bestScore = overlap;
      best = candidate;
    }
  }
  return best;
}

/**
 * 레이어를 싱크 라인에 붙인다.
 *
 * 1) 텍스트 완전 일치 → 2) 라인의 부분 문자열(배치 모드가 만든 블록) →
 * 3) 시간 겹침이 가장 큰 라인 → 4) 실패.
 * 앞의 두 단계에서 후보가 여럿이면 시간으로 가른다.
 */
function matchLine(layer: TextLayerInfo, document: SyncDocument): LineMatch | null {
  const layerKey = comparisonKey(layer.text);
  if (layerKey === "") return null;

  const exact = document.lines
    .filter((line) => comparisonKey(line.text) === layerKey)
    .map((line) => ({ line, quality: "exact" as const, atoms: line.atoms }));
  if (exact.length > 0) return pickByOverlap(exact, layer);

  const partial: LineMatch[] = [];
  for (const line of document.lines) {
    const lineKey = comparisonKey(line.text);
    const offset = lineKey.indexOf(layerKey);
    if (offset < 0) continue;
    const atoms = sliceAtomsByVisibleRange(line, offset, offset + layerKey.length);
    if (atoms.length > 0) partial.push({ line, quality: "substring", atoms });
  }
  if (partial.length > 0) return pickByOverlap(partial, layer);

  let best: LineMatch | null = null;
  let bestOverlap = 0;
  for (const line of document.lines) {
    const overlap = Math.min(line.end, layer.outPoint) - Math.max(line.start, layer.inPoint);
    if (overlap <= bestOverlap) continue;
    // 레이어 구간에 실제로 드는 atom만 — 라인 전체를 가져오면 없는 글자의 시각이 섞인다.
    const atoms = line.atoms.filter((atom) => {
      const midpoint = (atom.start + atom.end) / 2;
      return midpoint >= layer.inPoint && midpoint < layer.outPoint;
    });
    bestOverlap = overlap;
    best = { line, quality: "time", atoms: atoms.length > 0 ? atoms : line.atoms };
  }
  return best;
}

/** 커팅을 막아야 하는 레이어인지. 막는 이유는 UI에 그대로 띄운다. */
export function cutBlocker(layer: TextLayerInfo): string | undefined {
  if (layer.locked) return "잠긴 레이어는 자를 수 없습니다.";
  if (layer.sourceTextKeys > 0) {
    return "Source Text에 키프레임이 있는 레이어는 자를 수 없습니다. 키를 지우고 다시 시도하세요.";
  }
  if (/[\r\n]/.test(layer.text)) {
    return "여러 줄 텍스트는 자를 수 없습니다. 줄마다 레이어를 나눈 뒤 시도하세요.";
  }
  if (Array.from(layer.text.replace(/\s/g, "")).length < 2) {
    return "글자가 두 개 이상이어야 자를 수 있습니다.";
  }
  return undefined;
}

export function buildCutSession(
  layer: TextLayerInfo,
  document: SyncDocument | null,
  script: PronScript = "hangul",
): CutSession {
  const blocked = cutBlocker(layer);
  const match = document ? matchLine(layer, document) : null;
  // 발음은 고른 표기로만 보여준다. 표기가 없으면 줄 자체를 생략한다 —
  // 레거시 슬롯은 한글 값이라 romaji·kana 사용자에게 주면 안 된다.
  const pronunciation = match ? resolvedPronunciation(match.line, script) : undefined;
  const atoms = match?.atoms ?? [];
  const chars = assignCharTimings(
    layer.text,
    atoms,
    match?.quality === "exact" || match?.quality === "substring"
      ? (atoms[0]?.start ?? layer.inPoint)
      : layer.inPoint,
    layer.outPoint,
  );

  return {
    layerIndex: layer.index,
    layerName: layer.name,
    text: layer.text,
    inPoint: layer.inPoint,
    outPoint: layer.outPoint,
    chars,
    matchQuality: match?.quality ?? "none",
    ...(match ? { lineText: match.line.text } : {}),
    ...(pronunciation ? { pronunciation } : {}),
    ...(match?.line.translation ? { translation: match.line.translation } : {}),
    ...(blocked ? { blocked } : {}),
  };
}

/** 컷을 놓을 수 있는 글자 사이 위치. 양끝은 제외한다. */
export function cutCandidates(session: CutSession): number[] {
  const positions: number[] = [];
  for (let index = 1; index < session.chars.length; index += 1) positions.push(index);
  return positions;
}

/** index 지점의 기본 컷 시각 — 앞 글자가 끝나고 뒤 글자가 시작하는 사이. */
export function defaultCutTime(session: CutSession, index: number): number {
  const before = session.chars[index - 1];
  const after = session.chars[index];
  if (!before || !after) return session.inPoint;
  const raw = after.start <= before.end ? after.start : (before.end + after.start) / 2;
  // 라인 매칭이 어긋나면 atom 시각이 레이어 구간 밖일 수 있다. 그대로 두면 조각이 곡의
  // 엉뚱한 지점으로 날아가므로 레이어 안으로 가둔다.
  return Math.min(session.outPoint, Math.max(session.inPoint, raw));
}

function sortCuts(cuts: CutPoint[]): CutPoint[] {
  return [...cuts].sort((a, b) => a.index - b.index);
}

/** 이미 있으면 지우고(되붙이기), 없으면 기본 시각으로 만든다. */
export function toggleCut(session: CutSession, cuts: CutPoint[], index: number): CutPoint[] {
  if (index <= 0 || index >= session.chars.length) return cuts;
  const existing = cuts.find((cut) => cut.index === index);
  if (existing) return cuts.filter((cut) => cut.index !== index);
  const next: CutPoint = { index, time: defaultCutTime(session, index), auto: true };
  return sortCuts([...cuts, next]);
}

/** 드래그로 옮긴 컷 시각을 이웃 컷·레이어 경계 안으로 가둔다. */
export function clampCutTime(session: CutSession, cuts: CutPoint[], index: number, time: number): number {
  const sorted = sortCuts(cuts.filter((cut) => cut.index !== index));
  let lower = session.inPoint;
  let upper = session.outPoint;
  for (const cut of sorted) {
    if (cut.index < index) lower = Math.max(lower, cut.time);
    if (cut.index > index) upper = Math.min(upper, cut.time);
  }
  const min = lower + MIN_PIECE_SEC;
  const max = upper - MIN_PIECE_SEC;
  if (min >= max) return (lower + upper) / 2;
  return Math.min(max, Math.max(min, time));
}

export function moveCut(session: CutSession, cuts: CutPoint[], index: number, time: number): CutPoint[] {
  return sortCuts(
    cuts.map((cut) =>
      cut.index === index ? { ...cut, time: clampCutTime(session, cuts, index, time), auto: false } : cut,
    ),
  );
}

/**
 * 컷을 조각으로 편다.
 *
 * `reveal`이 등장 방식을 가른다:
 * - `cumulative`(기본) — 조각이 제 시각에 나타나 **줄이 끝날 때까지 남는다**. 조각을 원래
 *   글자 자리에 두는 것과 짝을 이뤄, 한 줄이 왼쪽부터 차례로 채워진다.
 * - `sequential` — 다음 조각이 나오면 앞 조각이 사라진다. 조각을 한자리에 겹쳐 놓고
 *   갈아 끼우는 연출에 쓴다.
 *
 * 어느 쪽이든 원본 구간을 넘기지 않는다. 조각 텍스트의 양끝 공백은 지우고 그만큼
 * charStart/charEnd를 좁힌다(x 좌표는 실제로 그려지는 첫 글자를 기준으로 해야 한다).
 */
export function computePieces(
  session: CutSession,
  cuts: CutPoint[],
  reveal: CutReveal = "cumulative",
): CutPiece[] {
  const sorted = sortCuts(cuts).filter((cut) => cut.index > 0 && cut.index < session.chars.length);
  const boundaries = [0, ...sorted.map((cut) => cut.index), session.chars.length];
  const pieces: CutPiece[] = [];

  for (let index = 0; index < boundaries.length - 1; index += 1) {
    const rawFrom = boundaries[index] ?? 0;
    const rawTo = boundaries[index + 1] ?? session.chars.length;
    if (rawTo <= rawFrom) continue;

    let from = rawFrom;
    let to = rawTo;
    while (from < to && isWhitespace(session.chars[from]?.char ?? "")) from += 1;
    while (to > from && isWhitespace(session.chars[to - 1]?.char ?? "")) to -= 1;
    if (to <= from) continue;

    const text = session.chars
      .slice(from, to)
      .map((entry) => entry.char)
      .join("");
    pieces.push({
      text,
      head: session.chars
        .slice(0, to)
        .map((entry) => entry.char)
        .join(""),
      start: index === 0 ? session.inPoint : (sorted[index - 1]?.time ?? session.inPoint),
      end:
        reveal === "cumulative" || index === boundaries.length - 2
          ? session.outPoint
          : (sorted[index]?.time ?? session.outPoint),
      charStart: from,
      charEnd: to,
    });
  }

  return pieces;
}

/** 조각 목록에서 사용자에게 알려야 할 문제. 비어 있으면 적용해도 된다. */
export function pieceWarnings(session: CutSession, pieces: CutPiece[]): string[] {
  const warnings: string[] = [];
  if (pieces.length < 2) warnings.push("자를 지점을 하나 이상 선택하세요.");
  for (const piece of pieces) {
    if (piece.end - piece.start < MIN_PIECE_SEC) {
      warnings.push(`「${piece.text}」 구간이 한 프레임보다 짧습니다.`);
    }
  }
  if (session.matchQuality === "none") {
    warnings.push("싱크 라인을 찾지 못해 레이어 구간을 글자 수로 균등 배분했습니다. 시각을 확인하세요.");
  }
  return warnings;
}
