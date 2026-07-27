"""자막 스캐폴드 회귀 — 붕괴 곡의 줄 시작을 자막 시각으로 고정하는 «결과 교체»를 못박는다.

배경: 자막을 CTC 제약(마스크)으로 쓰는 길은 두 번 실패했다(zyRt 7.1→25.6→29.1s —
settings.caption_anchors description). 스캐폴드는 DP에 묻지 않고 결과를 고치므로
전부 순수 산술이고, 여기서 그 산술의 계약을 고정한다.

못박는 것: 붕괴 배치가 자막 시각으로 이동하는가, 관용치 안 CTC(kept)는 손대지 않는가,
공유 이벤트의 후속 줄이 0길이로 쌓이지 않는가, 미매칭 줄이 순서에 맞으면 유지되는가,
결과가 항상 단조·비중첩·최소길이를 지키는가, 워커 게이트가 발동/스킵 사유를 남기는가.
"""

import pytest

from everyric2.alignment.caption_scaffold import ScaffoldLine, drift_seconds, scaffold_plan


def _spans(pairs):
    return [(float(s), float(e)) for s, e in pairs]


def _assert_wellformed(plan: list[ScaffoldLine]):
    for k, ln in enumerate(plan):
        assert ln.end >= ln.start + 0.1 - 1e-9, f"줄{k} 길이가 최소 미달"
        if k + 1 < len(plan):
            assert plan[k + 1].start >= ln.start, f"줄{k}→{k + 1} 시작 역전"
            assert ln.end <= plan[k + 1].start + 0.1 + 1e-9, f"줄{k}이 다음 줄을 과도 침범"


# ── 기전: 붕괴 배치 → 자막 골격 ──────────────────────────────────


def test_collapsed_lines_snap_to_caption_starts():
    """사고 서명 재현: 뒤에서 불리는 줄들이 앞에 뭉쳐 있다 → 자막 시각으로 이동."""
    # 실제로는 30/60/90초에 불리는 세 줄을 CTC가 전부 10초 부근에 뭉쳐 놓았다
    spans = _spans([(10.0, 11.0), (11.0, 12.0), (12.0, 13.0)])
    anchors = {0: (30.0, 33.0), 1: (60.0, 63.0), 2: (90.0, 93.0)}
    plan = scaffold_plan(spans, anchors, audio_sec=120.0, tolerance_sec=1.0)
    assert [round(p.start, 2) for p in plan] == [30.0, 60.0, 90.0]
    assert all(p.source == "caption" for p in plan)
    _assert_wellformed(plan)


def test_agreeing_lines_are_kept_untouched():
    """관용치 안이면 CTC가 자막 표시 시각보다 정밀하다 — 시작·끝 모두 그대로."""
    spans = _spans([(30.4, 32.9), (60.2, 62.8)])
    anchors = {0: (30.0, 33.0), 1: (60.0, 63.0)}
    plan = scaffold_plan(spans, anchors, audio_sec=90.0, tolerance_sec=1.0)
    assert plan[0] == ScaffoldLine(30.4, 32.9, "kept")
    assert plan[1] == ScaffoldLine(60.2, 62.8, "kept")


def test_shared_event_lines_do_not_stack_at_zero_length():
    """한 자막 이벤트가 우리 두 줄을 담으면(시작 동일) 후속 줄은 보간으로 편다."""
    spans = _spans([(5.0, 6.0), (5.5, 6.5), (40.0, 41.0)])
    anchors = {0: (20.0, 26.0), 1: (20.0, 26.0), 2: (40.0, 43.0)}
    plan = scaffold_plan(spans, anchors, audio_sec=60.0, tolerance_sec=1.0)
    assert plan[0].start == pytest.approx(20.0)
    assert plan[0].source == "caption"
    assert plan[1].source == "interp"
    assert plan[1].start > plan[0].start + 0.05
    assert plan[2].source == "kept"  # 40.0은 자기 앵커와 일치
    _assert_wellformed(plan)


def test_unmatched_line_keeps_ctc_when_it_fits_between_anchors():
    """미매칭 줄이라도 앞뒤 앵커 사이에 순서 모순 없이 들어가면 CTC를 유지한다 — 최소 개입."""
    spans = _spans([(10.2, 12.0), (14.8, 16.5), (30.1, 32.0)])
    anchors = {0: (10.0, 13.0), 2: (30.0, 33.0)}  # 가운데 줄만 미매칭
    plan = scaffold_plan(spans, anchors, audio_sec=45.0, tolerance_sec=1.0)
    assert plan[1].source == "kept"
    assert plan[1].start == pytest.approx(14.8)


def test_unmatched_line_gets_even_slot_when_ctc_contradicts_order():
    """미매칭 줄의 CTC가 구간 밖(뒤 앵커보다 뒤)이면 균등 슬롯으로 보간한다."""
    spans = _spans([(10.2, 12.0), (55.0, 56.0), (30.1, 32.0)])
    anchors = {0: (10.0, 13.0), 2: (30.0, 33.0)}
    plan = scaffold_plan(spans, anchors, audio_sec=45.0, tolerance_sec=1.0)
    assert plan[1].source == "interp"
    assert plan[0].start < plan[1].start < plan[2].start
    _assert_wellformed(plan)


def test_trailing_unmatched_lines_spread_to_audio_end():
    spans = _spans([(5.0, 6.0), (100.0, 101.0), (101.0, 102.0)])
    anchors = {0: (5.0, 8.0)}
    plan = scaffold_plan(spans, anchors, audio_sec=120.0, tolerance_sec=1.0)
    assert plan[0].source == "kept"
    # 뒤 두 줄의 CTC(100·101s)는 [5, 120] 구간 안에 순서대로 들어간다 — 유지
    assert plan[1].source == "kept" and plan[2].source == "kept"
    _assert_wellformed(plan)


def test_caption_line_end_uses_display_end_but_never_crosses_next_start():
    spans = _spans([(0.0, 1.0), (2.0, 3.0)])
    # 자막 표시가 다음 줄 시작(20s)을 넘겨 25s까지 남아 있는 관습 — 끝은 다음 시작 앞에서 잘린다
    anchors = {0: (10.0, 25.0), 1: (20.0, 22.0)}
    plan = scaffold_plan(spans, anchors, audio_sec=40.0, tolerance_sec=1.0)
    assert plan[0].start == pytest.approx(10.0)
    assert plan[0].end <= plan[1].start
    _assert_wellformed(plan)


def test_drift_seconds_only_counts_matched_lines_with_timing():
    spans = [(10.0, 11.0), (None, None), (40.0, 41.0)]
    anchors = {0: (30.0, 33.0), 1: (35.0, 36.0), 2: (41.0, 44.0)}
    d = drift_seconds(spans, anchors)
    assert d == [pytest.approx(20.0), pytest.approx(1.0)]


# ── 워커 게이트: 발동·스킵 판정과 감사 기록 ─────────────────────────


def _mk_results(pairs):
    from everyric2.inference.prompt import SyncResult

    return [
        SyncResult(line_number=i + 1, text=f"line{i}", start_time=s, end_time=e)
        for i, (s, e) in enumerate(pairs)
    ]


def _mk_plan(line_spans, rate=0.93):
    from everyric2.alignment.caption_anchors import AnchorPlan

    return AnchorPlan(line_spans=line_spans, debug={"rate": rate, "track": "ja"})


def _settings():
    from everyric2.config.settings import AlignmentSettings

    return AlignmentSettings()


def test_worker_gate_applies_on_conf_collapse_and_labels_lines():
    from everyric2.server.worker import _apply_caption_scaffold

    results = _mk_results([(10.0, 11.0), (11.0, 12.0), (12.0, 13.0)])
    fixes: dict[int, list[str]] = {}
    meta = _apply_caption_scaffold(
        results,
        None,
        fixes,
        _mk_plan({0: (30.0, 33.0), 1: (60.0, 63.0), 2: (90.0, 93.0)}),
        song_conf=0.0005,  # 붕괴 영역 (실측 3곡 전부 < 0.002)
        audio_sec=120.0,
        align_settings=_settings(),
    )
    assert meta["applied"] is True
    assert meta["gates"] == {"conf": True, "drift": True}
    assert meta["sources"]["caption"] == 3
    assert [r.start_time for r in results] == [pytest.approx(30.0), pytest.approx(60.0), pytest.approx(90.0)]
    assert all("scaffold" in fixes[i] for i in range(3))


def test_worker_gate_skips_healthy_song_and_records_why():
    from everyric2.server.worker import _apply_caption_scaffold

    results = _mk_results([(29.9, 32.0), (60.3, 62.0)])
    meta = _apply_caption_scaffold(
        results,
        None,
        {},
        _mk_plan({0: (30.0, 33.0), 1: (60.0, 63.0)}),
        song_conf=0.02,  # 코퍼스 중앙값대 — 정상
        audio_sec=90.0,
        align_settings=_settings(),
    )
    assert meta == {**meta, "applied": False, "skipped": "not_collapsed"}
    assert results[0].start_time == pytest.approx(29.9)  # 아무것도 안 건드렸다


def test_worker_gate_drift_alone_triggers_even_with_ok_conf():
    from everyric2.server.worker import _apply_caption_scaffold

    results = _mk_results([(10.0, 11.0), (11.5, 12.5), (13.0, 14.0)])
    fixes: dict[int, list[str]] = {}
    meta = _apply_caption_scaffold(
        results,
        None,
        fixes,
        _mk_plan({0: (30.0, 33.0), 1: (60.0, 63.0), 2: (90.0, 93.0)}),
        song_conf=0.02,
        audio_sec=120.0,
        align_settings=_settings(),
    )
    assert meta["gates"] == {"conf": False, "drift": True}
    assert meta["applied"] is True


def test_span_gate_is_lower_than_positive_gate():
    """매칭 0.7~0.85 구간: 양성 제약(line_starts)은 접고 스캐폴드(line_spans)만 선다 —
    消失(76.9%, 병합·표기차로 미달)이 스킵되던 실측 사고의 회귀 고정."""
    from everyric2.alignment.caption_anchors import derive_anchor_plan

    lines = [f"かなり長い歌詞の行{i}です" for i in range(10)]
    events = [
        {"text": lines[i], "start": 10.0 * i, "end": 10.0 * i + 3.0} for i in range(8)
    ]  # 8/10 = 0.8 매칭
    plan = derive_anchor_plan(
        lines, [("ja", events)], positive_min_match=0.85, span_min_match=0.7
    )
    assert plan.line_starts == {}  # 양성 제약은 0.85 미달로 접힌다
    assert len(plan.line_spans) == 8  # 스캐폴드는 0.7 하한으로 선다
    assert plan.debug["positive_skipped"] == "low_match"


def test_worker_gate_reports_missing_anchor_reason():
    from everyric2.alignment.caption_anchors import AnchorPlan
    from everyric2.server.worker import _apply_caption_scaffold

    meta = _apply_caption_scaffold(
        _mk_results([(0.0, 1.0)]),
        None,
        {},
        AnchorPlan(debug={"positive_skipped": "low_match"}),
        song_conf=0.0005,
        audio_sec=60.0,
        align_settings=_settings(),
    )
    assert meta == {"applied": False, "skipped": "low_match"}
