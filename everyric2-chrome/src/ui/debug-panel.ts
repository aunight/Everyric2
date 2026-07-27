import type { LyricLine, SyncDebugMeta } from '../types';
import { h } from './dom';

/**
 * 곡 전체 디버그 패널 — 라인 집중 뷰(pip.ts renderTimingLanes)의 자매 기능이다.
 * 그쪽이 "지금 재생 중인 한 줄"을 확대해 보여준다면, 이 패널은 "곡 전체를 한눈에" 훑어
 * 원문 vs heard(CTC가 실제로 들은 것)를 전수 대비하는 용도다. 패널은 UI만 만들고 상태를
 * 갖지 않는다 — 시크는 호출부(overlay.ts)가 콜백으로 주입한다.
 */

/** 3색 정렬 신뢰도 등급 — pip.ts confBucketColor·overlay.css .ey-conf-*와 같은 경계값(재사용) */
function confGrade(conf: number | undefined): { cls: string; label: string } | null {
  if (conf == null) return null;
  if (conf < 1e-4) return { cls: 'ey-conf-low', label: '낮음' };
  if (conf < 2e-2) return { cls: 'ey-conf-mid', label: '보통' };
  return { cls: 'ey-conf-ok', label: '좋음' };
}

/** updateDebug의 시각 표기(초 2자리)와 같은 형식으로 통일 */
function fmtTime(t: number | null): string {
  return t === null ? '-' : `${t.toFixed(2)}s`;
}

/** 곡 단위 자막 스캐폴드 요약 한 줄 — 없으면(구서버·미배선·해당 없음) null로 생략된다 */
function scaffoldSummary(meta: SyncDebugMeta | null | undefined): string | null {
  const sc = meta?.caption_scaffold;
  if (!sc) return null;
  if (sc.applied) {
    const src = sc.sources ?? {};
    const total = (src.caption ?? 0) + (src.interp ?? 0) + (src.kept ?? 0);
    const matchPct = total > 0 ? Math.round(((src.caption ?? 0) / total) * 100) : 0;
    return `자막 스캐폴드 적용됨 — ${sc.moved ?? 0}줄 이동 (자막고정 ${matchPct}% · 고정${src.caption ?? 0}·보간${src.interp ?? 0}·유지${src.kept ?? 0})`;
  }
  // not_collapsed(정상 곡이라 애초에 시도조차 안 함)는 소음이라 생략 — pip.ts 디버그 오버레이와 같은 판단
  if (sc.skipped && sc.skipped !== 'not_collapsed') {
    return `자막 스캐폴드 안 씀 — ${sc.skipped}`;
  }
  return null;
}

export interface DebugPanelRefs {
  el: HTMLDivElement;
}

/**
 * @param onSeek 이미 SEEK_INTO_LINE_SEC 같은 보정이 필요하면 호출부가 콜백 안에서 적용한다 —
 *   이 함수는 line.time을 그대로 넘긴다.
 */
export function buildDebugPanel(
  lines: LyricLine[],
  debugMeta: SyncDebugMeta | null | undefined,
  onSeek: (time: number) => void,
): DebugPanelRefs {
  const el = h('div', { className: 'ey-debug-panel' });

  const summary = scaffoldSummary(debugMeta);
  if (summary) {
    el.append(h('div', { className: 'ey-debug-panel-summary', text: summary }));
  }

  if (lines.length === 0) {
    el.append(h('div', { className: 'ey-debug-panel-empty', text: '표시할 라인이 없어요' }));
    return { el };
  }

  const list = h('div', { className: 'ey-debug-panel-list' });
  for (const line of lines) {
    const dbg = line.debug;
    const grade = confGrade(line.confidence);

    const chip = h('span', {
      className: `ey-debug-row-chip${grade ? ` ${grade.cls}` : ''}`,
      text: grade ? grade.label : '—',
    });

    const textCol = h('div', { className: 'ey-debug-row-text' },
      h('div', { className: 'ey-debug-row-orig', text: line.text }));
    // heard(CTC가 실제로 들은 것) — 원문과 구분되게 흐린 색으로, 있을 때만
    if (dbg?.heard) {
      textCol.append(h('div', { className: 'ey-debug-row-heard', text: `들림: ${dbg.heard}` }));
    }
    // fixes 라벨 + 심판 개입(⚖) — 한 줄에 이어 붙인다(있는 것만)
    const labels: string[] = [];
    if (dbg?.fixes && dbg.fixes.length > 0) labels.push(dbg.fixes.join('·'));
    const ref = dbg?.referee;
    if (ref?.chosen && ref.chosen !== ref.default) {
      labels.push(`⚖ ${ref.default ?? '?'}→${ref.chosen}`);
    }
    if (labels.length > 0) {
      textCol.append(h('div', { className: 'ey-debug-row-labels', text: labels.join(' · ') }));
    }

    const row = h('button', {
      className: 'ey-debug-row',
      attrs: { type: 'button' },
      title: '클릭해서 이 구간으로 이동',
      on: {
        click: () => {
          if (line.time !== null) onSeek(line.time);
        },
      },
    }, h('span', { className: 'ey-debug-row-time', text: fmtTime(line.time) }), chip, textCol);

    list.append(row);
  }
  el.append(list);
  return { el };
}
