"""저신뢰 ja/ko 이중정렬 안전망 회귀 테스트.

커버리지≥0.9면 독음(ko) 정렬이 성공하는 한 품질과 무관하게 유지되던 구조에, ko 곡 단위
평균 신뢰도가 바닥이면 원문(ja) 정렬도 돌려 명확히 나은 쪽을 채택하는 안전망을 추가했다.
실측: 熱異常 원곡 ko quality 0.0005 vs 커버 0.0076 — 임계 0.002가 그 사이.
"""
from everyric2.inference.prompt import SyncResult, WordSegment
from everyric2.server.worker import (
    _avg_line_confidence,
    _dual_align_prefers_original,
    _dual_align_should_run,
)


def _line(conf=None, words=None):
    ws = None
    if words is not None:
        ws = [WordSegment(word="x", start=0.0, end=0.1, confidence=c) for c in words]
    return SyncResult(text="x", start_time=0.0, end_time=1.0, confidence=conf, word_segments=ws)


# ---- _avg_line_confidence: 라인 conf 또는 글자 conf 기하평균 ----------------------


def test_avg_line_confidence_uses_line_conf_then_word_geomean():
    # 라인 conf 있으면 그대로, 없으면 word conf 기하평균으로 보충
    lines = [_line(conf=0.004), _line(words=[0.01, 0.04])]  # 두 번째: geomean=0.02
    avg = _avg_line_confidence(lines)
    assert abs(avg - (0.004 + 0.02) / 2) < 1e-9


def test_avg_line_confidence_none_when_no_confidence():
    assert _avg_line_confidence([_line(), _line()]) is None
    assert _avg_line_confidence([]) is None


# ---- 비용 게이트: ko 신뢰도가 임계 미만일 때만 ja 실행 ---------------------------


def test_should_run_only_below_threshold():
    # (b) ko 정상(임계 이상) → ja 미실행
    assert _dual_align_should_run(0.0076, 0.002) is False  # 커버 수준
    # (a/c) ko 바닥(임계 미만) → ja 실행
    assert _dual_align_should_run(0.0005, 0.002) is True   # 熱異常 원곡 수준


def test_should_run_disabled_and_none():
    assert _dual_align_should_run(0.0005, 0.0) is False   # 임계 0 = 비활성
    assert _dual_align_should_run(None, 0.002) is False    # conf 없음


# ---- 채택 결정: ja가 min_ratio배 이상 나을 때만 원문 채택 -----------------------


def test_prefers_original_when_ja_clearly_better():
    # (a) ko 저신뢰 + ja 우위(>=1.5배) → 원문 채택
    assert _dual_align_prefers_original(0.0005, 0.005, 1.5) is True


def test_keeps_ko_when_ja_marginal_or_worse():
    # (c) 熱異常: ja도 같이 붕괴(비슷) → margin 미달로 ko 유지
    assert _dual_align_prefers_original(0.0005, 0.0006, 1.5) is False
    assert _dual_align_prefers_original(0.0005, 0.0004, 1.5) is False  # ja 더 나쁨
    assert _dual_align_prefers_original(0.0005, None, 1.5) is False    # ja conf 없음
    # 정확히 1.5배 경계는 채택
    assert _dual_align_prefers_original(0.0010, 0.0015, 1.5) is True


def test_netsu_threshold_separates_original_from_cover():
    # 熱異常 실측 수치로 게이트 경계 고정: 원곡은 이중정렬 실행, 커버는 미실행
    assert _dual_align_should_run(0.0005, 0.002) is True    # 원곡 → ja 대조
    assert _dual_align_should_run(0.0076, 0.002) is False   # 커버 → 비용 절약, ko 유지
