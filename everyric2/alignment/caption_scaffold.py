"""자막 타이밍 스캐폴드 — 붕괴 곡의 «최소 기본 앵커».

## 왜 제약이 아니라 골격인가 (실측 2026-07-26·27)

자막을 CTC의 **제약**으로 쓰는 길은 두 번 실패했다. 금지 마스크는 zyRt-nBM3dY에서
7.1s → 25.6s로, star 가격을 더해도 29.1s로 악화됐다 — 붕괴된(균일 바닥) posterior
위에서 마스크는 동점을 옮길 뿐이고, 쫓겨난 줄을 붙들 봉우리가 emission에 없다.
채택 게이트도 음향 근거를 비교하므로 평평한 emission에서는 장님이다
(``settings.caption_anchors`` description의 실측 기록).

이 모듈은 반대로 간다: **줄 시작을 사람이 찍은 자막 시각으로 고정**하고 DP에게는
아무것도 묻지 않는다. 오차 상한이 줄 길이로 캡되고, 발동 게이트는 음향이 아니라
텍스트 매칭률과 시각 불일치 크기다(둘 다 워커가 판정). 실측 근거:

  · zyRt-nBM3dY: 자막 매칭 92.9%, 앵커 시각 자체는 ±0.2s급 (간주 앞 실측) —
    CTC는 자막 기준 mae 7.1s
  · VWVtIg5cdDU(消失): 매칭 76.9%, CTC mae 0.5s — 관용치 안이라 대부분 «유지(kept)»
    로 무개입, 가끔 수십 초 튀는 꼬리 줄만 잡힌다

자막 표시가 가창보다 이른 편향은 지각적으로 유리한 방향이다 (Deezer ISMIR 2021:
가사 선행 −0.3s vs 지연 +0.2s 임계 — 사람은 가사가 먼저 뜨는 쪽에 관대하다).

역할 분담은 SRT 해상도 실측과 일치한다: 자막은 «줄» 타이밍에만 유효하므로 여기서는
줄 경계만 만들고, 줄 안 음절 분배는 호출부가 균등 재합성으로 처리한다
(``worker._resynth_word_segments`` — 붕괴 곡의 라인 내부 CTC 분포는 무의미하다).

최소 개입 원칙: 자막과 관용치 안에서 일치하는 줄(kept)과, 앵커 사이 순서에 모순 없이
들어가 있는 미매칭 줄(kept)은 CTC 타이밍을 그대로 둔다 — CTC가 맞을 때는 음향 쪽이
자막 표시 시각보다 정밀하기 때문이다. 움직인 줄만 «scaffold» 보정 라벨을 받는다.

순수 함수만 있다 — torch도 IO도 없다. 자막 조달·매칭은 ``caption_anchors``가,
적용·재합성·게이트는 ``server.worker``가 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 줄이 이보다 짧아지지 않게 한다 — 겹침·역순만 막는 최소 보장
_MIN_LINE_SEC = 0.1
# 다음 줄 시작과의 최소 간격 — 경계가 정확히 겹치면 확장 하이라이트가 순간 겹친다
_LINE_GAP_SEC = 0.02
# 자막 표시 종료는 다음 줄까지 이어지는 관습이 흔해 끝 시각으로는 약한 신호다 —
# 시작 + 이 값에 못 미치면 최소 이만큼은 불렸다고 본다 (caption_anchors._MIN_EVENT_SEC 동일)
_MIN_SUNG_SEC = 0.5


@dataclass(frozen=True)
class ScaffoldLine:
    start: float
    end: float
    # caption = 자막 시각으로 고정 / interp = 앵커 사이 균등 보간 / kept = CTC 유지
    source: str


def drift_seconds(
    spans: list[tuple[float | None, float | None]],
    line_spans: dict[int, tuple[float, float]],
) -> list[float]:
    """매칭된 줄들의 |CTC 시작 − 자막 시작| — 발동 게이트(붕괴 판정 ②)의 입력."""
    out: list[float] = []
    for i, (a_start, _a_end) in sorted(line_spans.items()):
        if i < len(spans) and spans[i][0] is not None:
            out.append(abs(spans[i][0] - a_start))
    return out


def scaffold_plan(
    spans: list[tuple[float | None, float | None]],
    anchors: dict[int, tuple[float, float]],
    audio_sec: float,
    tolerance_sec: float = 1.0,
) -> list[ScaffoldLine]:
    """현재 정렬 + 자막 앵커 → 줄별 (start, end, source). 발동 여부는 호출부가 정한다.

    앵커 줄: 자막 시각 고정(관용치 안이면 CTC 유지). 한 자막 이벤트가 우리 여러 줄을
    담으면(시작 시각 동일) 첫 줄만 앵커로 쓰고 나머지는 보간으로 돌린다 — 같은 시각에
    줄을 쌓으면 앞줄 길이가 0이 된다. 미매칭 줄: 앞뒤 배정 시각 사이에서, CTC 시각이
    순서에 모순 없이 들어가면 유지하고 아니면 균등 슬롯을 준다.
    """
    n = len(spans)
    if n == 0:
        return []

    starts: list[float | None] = [None] * n
    source: list[str] = ["interp"] * n

    # ── 1) 앵커 줄의 시작 확정 ──
    prev_anchor_t: float | None = None
    for i in sorted(anchors):
        if i >= n:
            continue
        a_start = float(anchors[i][0])
        if prev_anchor_t is not None and a_start <= prev_anchor_t + 1e-6:
            continue  # 공유 이벤트의 후속 줄 — 아래 보간이 이벤트 창 안에 편다
        prev_anchor_t = a_start
        ctc = spans[i][0]
        if ctc is not None and abs(ctc - a_start) <= tolerance_sec:
            starts[i] = float(ctc)
            source[i] = "kept"
        else:
            starts[i] = a_start
            source[i] = "caption"

    if not any(s is not None for s in starts):
        # 앵커가 하나도 못 박혔다 — 호출부 게이트가 막아야 할 상황이지만 안전하게 항등
        return [
            ScaffoldLine(s if s is not None else 0.0, e if e is not None else 0.0, "kept")
            for s, e in spans
        ]

    # 단조 강제 — kept가 자막 순서를 거스르는 드문 경우 자막 쪽으로 넘긴다
    prev = 0.0
    for i in range(n):
        if starts[i] is None:
            continue
        if starts[i] < prev:
            starts[i] = prev
            if source[i] == "kept":
                source[i] = "caption"
        prev = starts[i]

    # ── 2) 미배정 줄: 앞뒤 배정 시각 사이 — CTC가 순서에 맞으면 유지, 아니면 균등 ──
    i = 0
    while i < n:
        if starts[i] is not None:
            i += 1
            continue
        j = i
        while j < n and starts[j] is None:
            j += 1
        lo = starts[i - 1] if i > 0 else 0.0
        hi = starts[j] if j < n else max(audio_sec, lo + (j - i + 1) * _MIN_LINE_SEC)
        cur = lo
        for k in range(i, j):
            remaining = j - k
            ctc = spans[k][0]
            fits = (
                ctc is not None
                and cur + _LINE_GAP_SEC <= ctc <= hi - remaining * _MIN_LINE_SEC
            )
            if fits:
                starts[k] = float(ctc)  # pyright: ignore[reportArgumentType]
                source[k] = "kept"
            else:
                starts[k] = cur + (hi - cur) / (remaining + 1)
                source[k] = "interp"
            cur = starts[k]
        i = j

    # ── 3) 끝 시각 ──
    out: list[ScaffoldLine] = []
    for i in range(n):
        start = float(starts[i])  # pyright: ignore[reportArgumentType]
        nxt = float(starts[i + 1]) if i + 1 < n else max(audio_sec, start + _MIN_LINE_SEC)
        cap = nxt - _LINE_GAP_SEC
        if source[i] == "kept":
            ctc_end = spans[i][1]
            end = min(ctc_end, cap) if ctc_end is not None else cap
        elif source[i] == "caption":
            # 자막 표시 종료(관습상 다음 줄까지 남기도 함)와 최소 가창 길이 중 신뢰 가능한 쪽
            a_end = float(anchors[i][1]) if i in anchors else start + _MIN_SUNG_SEC
            end = min(max(a_end, start + _MIN_SUNG_SEC), cap)
        else:  # interp — 다음 시작까지의 슬롯
            end = cap
        out.append(ScaffoldLine(start, max(end, start + _MIN_LINE_SEC), source[i]))
    return out
