"""``_greedy_spans`` 단위 테스트 — heard 텍스트에 글자별 시각을 붙이는 순수 함수.

GPU도 실오디오도 필요 없다: 이미 argmax를 마친 프레임 id 열을 직접 구성해서, 연속
중복·blank 붕괴 규칙이 ``_greedy_text``와 동형이고, 살아남은 각 토큰에 «그 토큰이 처음
등장한 프레임의 시각»(t0 + idx*frame_sec)이 붙는지만 못박는다.
"""

from everyric2.alignment.ctc_engine import _greedy_spans


def test_greedy_spans_collapse_and_time():
    # blank=0, frames: [0,5,5,0,7,0] → 토큰 5@frame1, 7@frame4
    spans = _greedy_spans([0, 5, 5, 0, 7, 0], 0, {5: "あ", 7: "い"}, frame_sec=0.02, t0=10.0)
    assert spans == [("あ", 10.02), ("い", 10.08)]


def test_greedy_spans_all_blank_is_empty():
    spans = _greedy_spans([0, 0, 0, 0], 0, {5: "あ"}, frame_sec=0.02, t0=0.0)
    assert spans == []


def test_greedy_spans_no_repeat_collapse_without_blank_between():
    # 반복이 blank로 끊기지 않아도(같은 id가 연속) 한 스팬으로 붕괴하고, 다른 id로
    # 바뀌면 그 시점의 프레임 시각으로 새 스팬이 시작한다.
    spans = _greedy_spans([5, 5, 5, 7, 7], 0, {5: "あ", 7: "い"}, frame_sec=0.02, t0=1.0)
    assert spans == [("あ", 1.0), ("い", 1.06)]
