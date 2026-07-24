"""독음(ko) 정렬 역방향 누출 가드 회귀 테스트 (모든 간주 + 선두 라인 누출).

初音ミクの消失(VWVtIg5cdDU) 2026-07-24 재실측: 벌크 정렬이 개선되며 실패 양상이
'간주 이후 블록 전체 압축'(전창 점유 마진으로 잡힘)에서 '대사 블록의 선두 라인만
간주 이전으로 역누출'(마진이 못 잡음)로 완화됐다. 실측 수치:
  - swallowed(_star_swallowed_vocal) = 1.02s  → 삼킴 게이트(8.0) 미달, 검사 시작조차 안 됨
  - post_win = 최대 간주(147.74→163.94) 하나만 앵커 → 나레이션1(간주 43.76→59.84 뒤) 밖
  - ko_fill = 103/116s → 창을 이미 채워 fill 마진 미달
누출 라인: idx17-18 ~19s 조기, idx46 ~24s 조기(idx19+/47+는 ~정위치).
이 테스트는 확장된 헬퍼가 두 대사 블록만 외과적으로 잡고 초고속 랩부의 ±2~3s 흔들림·
무해 케이스는 건드리지 않음을 합성 데이터로 고정한다 (GPU 불필요).
"""
from everyric2.audio.vad import VocalRegion
from everyric2.inference.prompt import SyncResult
from everyric2.server.worker import (
    _apply_leak_splice,
    _leaked_runs,
    _mark_leak_ghosts,
    _post_interlude_windows,
    _straddles_interlude,
)


def _regions(*spans: tuple[float, float]) -> list[VocalRegion]:
    return [VocalRegion(start=s, end=e, energy=0.1) for s, e in spans]


def _line(start: float, end: float) -> SyncResult:
    return SyncResult(text="x", start_time=start, end_time=end)


# ---- _post_interlude_windows: 모든 간주 ----------------------------------------


def test_windows_covers_all_interludes_not_just_largest():
    # VWVtIg5cdDU VAD 구조 축약: 간주 43.76→59.84(16.08s), 147.74→163.94(16.2s),
    # 259.34→270.68(11.34s). 최대 갭은 두 번째지만 세 간주 모두 창이 나와야 한다.
    regions = _regions(
        (27.48, 43.76), (59.84, 147.74), (163.94, 259.34), (270.68, 279.92)
    )
    windows = _post_interlude_windows(regions, min_gap_sec=5.0)
    assert windows == [(59.84, 147.74), (163.94, 259.34), (270.68, 279.92)]


def test_windows_empty_without_big_gap_and_single_when_one_gap():
    assert _post_interlude_windows(_regions((0.0, 20.0), (21.0, 40.0)), min_gap_sec=5.0) == []
    assert _post_interlude_windows(_regions((0.0, 20.0)), min_gap_sec=5.0) == []
    # 갭 하나(20→30 = 10s ≥ 5): 창 = [30, 마지막 발성 끝]
    assert _post_interlude_windows(
        _regions((0.0, 20.0), (30.0, 45.0)), min_gap_sec=5.0
    ) == [(30.0, 45.0)]


def test_straddles_interlude_flags_big_interline_gap():
    # 라인18 end 43.76 → 라인19 start 59.84 (16s 간극) = 간주를 가로지름
    lines = [_line(40.15, 43.76), _line(59.84, 66.0)]
    assert _straddles_interlude(lines, min_gap_sec=5.0) is True
    # 촘촘히 이어지는 라인만: straddle 아님
    assert _straddles_interlude([_line(0.0, 2.0), _line(2.1, 4.0)], min_gap_sec=5.0) is False


# ---- _leaked_runs: 선두 누출 + 전체 압축 모두 포착, 흔들림은 배제 -----------------


def _vwv_ko_ja():
    """VWVtIg5cdDU 실측 타이밍(대사 두 블록 + 이웃)을 인덱스 정합 ko/ja 쌍으로 축약."""
    #        idx  ko(start,end)       ja(start,end)        비고
    rows = [
        (27.48, 28.10, 27.32, 28.10),   # 0 인트로 — 정위치(disp .16)
        (39.63, 40.15, 42.10, 42.60),   # 1 라인16 — disp 2.47 (<3, 미변위: seed 아님)
        (40.15, 41.83, 59.28, 61.60),   # 2 나레이션1 선두 누출 — seed, disp 19.1
        (41.88, 43.76, 62.40, 64.40),   # 3 나레이션1 누출 — seed, disp 20.5
        (59.84, 66.00, 68.34, 74.50),   # 4 나레이션1 꼬리 — disp 8.5, seed 인접 → 런 포함
        (75.30, 82.00, 75.06, 82.00),   # 5 라인20 — 정위치(disp .24) → 런 경계
        (108.94, 115.0, 111.0, 115.0),  # 6 초고속 랩 — disp 2.06 (<3): 스플라이스 금지
        (140.31, 145.27, 163.98, 169.0),  # 7 나레이션2 선두 누출 — seed, disp 23.67
        (163.79, 167.6, 169.58, 175.0),  # 8 나레이션2 — disp 5.79, seed 인접 → 런 포함
        (167.67, 180.0, 175.36, 179.0),  # 9 나레이션2 — disp 7.69, seed 인접 → 런 포함
        (180.80, 182.0, 179.18, 182.0),  # 10 라인49 — 정위치(disp 1.62) → 런 경계
        (200.0, 205.0, 204.0, 209.0),   # 11 후반 랩 — disp 4 (>3) 이지만 seed 아님 → 배제
    ]
    ko = [_line(a, b) for a, b, _c, _d in rows]
    ja = [_line(c, d) for _a, _b, c, d in rows]
    return ko, ja


def test_leaked_runs_captures_exactly_the_two_narration_blocks():
    ko, ja = _vwv_ko_ja()
    windows = [(59.84, 147.74), (163.94, 259.34)]
    runs = _leaked_runs(ko, ja, windows, lead_sec=3.0)
    assert runs == [[2, 3, 4], [7, 8, 9]]
    # 워커 측 leak_min(8.0) 필터: 두 런 모두 최악 변위 ≥ 8 → 채택
    for run in runs:
        assert max(ja[k].start_time - ko[k].start_time for k in run) >= 8.0


def test_leaked_runs_excludes_seedless_and_minor_wobble():
    ko, ja = _vwv_ko_ja()
    windows = [(59.84, 147.74), (163.94, 259.34)]
    leaked = {i for run in _leaked_runs(ko, ja, windows, lead_sec=3.0) for i in run}
    # 초고속 랩(6, disp 2.06)·라인16(1, disp 2.47)·seed 없는 후반 변위(11, disp 4)는 제외
    assert leaked.isdisjoint({0, 1, 5, 6, 10, 11})


def test_leaked_runs_benign_case_stays_below_threshold():
    # 熱異常류: 간주 이후 첫 라인이 ko/ja 모두 제자리(작은 변위). 큰 누출 런 없음.
    windows = [(60.0, 120.0)]
    ko = [_line(20.0, 30.0), _line(62.0, 70.0), _line(72.0, 80.0)]
    ja = [_line(20.0, 30.0), _line(64.5, 72.0), _line(74.0, 82.0)]  # 최대 disp 2.5s
    runs = _leaked_runs(ko, ja, windows, lead_sec=3.0)
    # 런이 생겨도(seed 없음) 최악 변위가 leak_min(8) 미만 → 워커가 이동하지 않음
    leaked = [
        i
        for run in runs
        if max(ja[k].start_time - ko[k].start_time for k in run) >= 8.0
        for i in run
    ]
    assert leaked == []


def test_leaked_runs_guards_mismatched_lengths():
    assert _leaked_runs([_line(0.0, 1.0)], [], [(5.0, 10.0)], lead_sec=3.0) == []
    assert _leaked_runs([], [], [], lead_sec=3.0) == []


# ---- _apply_leak_splice: 누출 라인 타이밍만 ja로 교체, 나머지 보존 -----------------


def test_apply_leak_splice_replaces_only_leaked_timing():
    ko, ja = _vwv_ko_ja()
    before_5 = (ko[5].start_time, ko[5].end_time)
    before_6 = (ko[6].start_time, ko[6].end_time)
    _apply_leak_splice(ko, ja, [2, 3, 4, 7, 8, 9])
    # 누출 라인은 ja 타이밍으로 교체
    assert (ko[2].start_time, ko[2].end_time) == (ja[2].start_time, ja[2].end_time)
    assert (ko[7].start_time, ko[7].end_time) == (ja[7].start_time, ja[7].end_time)
    # 비누출 라인(초고속 랩·정위치)은 그대로
    assert (ko[5].start_time, ko[5].end_time) == before_5
    assert (ko[6].start_time, ko[6].end_time) == before_6


# ---- _mark_leak_ghosts: 디버그 고스트(원 ko 위치) + "leak" 라벨 -------------------


def test_mark_leak_ghosts_labels_only_moved_lines():
    ko, ja = _vwv_ko_ja()
    pre = {i: (ko[i].start_time, ko[i].end_time) for i in [2, 3, 4]}  # 스플라이스 전 ko
    _apply_leak_splice(ko, ja, [2, 3, 4])  # 이제 ko[2..4]는 ja 타이밍
    raw_spans = [(r.start_time, r.end_time) for r in ko]  # 리셋된 raw
    fixes: dict[int, list[str]] = {}
    _mark_leak_ghosts(raw_spans, fixes, pre, ko)
    # 이동한 라인은 원 ko 위치를 고스트로 복원 + "leak" 라벨
    assert raw_spans[2] == pre[2] and fixes[2] == ["leak"]
    assert raw_spans[3] == pre[3] and fixes[3] == ["leak"]
    # pre에 없던 라인은 손대지 않음
    assert 5 not in fixes


def test_mark_leak_ghosts_prepends_and_dedups_label():
    ko, ja = _vwv_ko_ja()
    pre = {2: (ko[2].start_time, ko[2].end_time)}
    _apply_leak_splice(ko, ja, [2])
    raw_spans = [(r.start_time, r.end_time) for r in ko]
    fixes = {2: ["pp"]}  # 이미 다른 라벨이 있어도
    _mark_leak_ghosts(raw_spans, fixes, pre, ko)
    assert fixes[2] == ["leak", "pp"]  # 앞에 삽입, 중복 없음
    _mark_leak_ghosts(raw_spans, fixes, pre, ko)  # 재호출해도 중복 안 됨
    assert fixes[2] == ["leak", "pp"]


def test_mark_leak_ghosts_skips_unmoved_within_tol():
    ko, _ = _vwv_ko_ja()
    pre = {2: (ko[2].start_time, ko[2].end_time)}  # 이동 안 함
    raw_spans = [(r.start_time, r.end_time) for r in ko]
    fixes: dict[int, list[str]] = {}
    _mark_leak_ghosts(raw_spans, fixes, pre, ko)
    assert fixes == {}  # 움직이지 않은 라인은 라벨 없음
