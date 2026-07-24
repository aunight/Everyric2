"""본문 글자 완전 커버리지 words 재구성(_full_coverage_words) 회귀 테스트.

정렬 word_segments는 정규화 텍스트 기준이라 본문의 공백·표기 차이 글자를 빠뜨려
''.join(words)가 본문과 어긋나면 확장의 글자 매핑(indexOf)이 죽고 라인이 통짜로 점등됐다.
재구성 후에는 words[].word를 순서대로 이으면 정확히 본문이 되고, 타이밍은 단조 비감소이며,
덮이지 않던 글자는 인접 토큰 시각으로 보간(confidence=None)된다. pron_segments는 불변.
"""
from everyric2.inference.prompt import WordSegment
from everyric2.server.worker import _full_coverage_words


def _seg(word, start, end, conf=0.5):
    return WordSegment(word=word, start=start, end=end, confidence=conf)


def _join(out):
    return "".join(o["word"] for o in out)


def _monotonic(out):
    s = [o["start"] for o in out]
    return all(s[i + 1] >= s[i] - 1e-9 for i in range(len(s) - 1))


def test_reinserts_dropped_space_and_preserves_timing():
    text = "ボクは生まれ そして気づく"  # 본문에 전각 아닌 반각 공백 포함
    # 정렬 토큰: 공백을 뺀 글자 단위
    chars = "ボクは生まれそして気づく"
    toks = [_seg(c, 27.0 + i * 0.1, 27.0 + i * 0.1 + 0.05, conf=0.001 + i * 1e-4) for i, c in enumerate(chars)]
    out = _full_coverage_words(text, toks)
    assert _join(out) == text
    assert _monotonic(out)
    # 공백은 보간(confidence None), 나머지 글자는 원 토큰 confidence 상속
    space = [o for o in out if o["word"] == " "]
    assert len(space) == 1 and space[0]["confidence"] is None
    assert all(o["confidence"] is not None for o in out if o["word"] != " ")


def test_covers_fullwidth_paren_reading_and_space():
    text = "永遠（トワ）の命 「VOCALOID」"  # 괄호 독음 + 공백 혼합
    chars = "永遠（トワ）の命「VOCALOID」"  # 공백만 빠진 토큰
    toks = [_seg(c, 30.0 + i * 0.1, 30.0 + i * 0.1 + 0.05) for i, c in enumerate(chars)]
    out = _full_coverage_words(text, toks)
    assert _join(out) == text
    assert _monotonic(out)


def test_matched_line_is_unchanged_and_inherits_confidence():
    text = "知ってなおも"
    toks = [_seg(c, 29.0 + i * 0.2, 29.0 + i * 0.2 + 0.1, conf=0.01 * (i + 1)) for i, c in enumerate(text)]
    out = _full_coverage_words(text, toks)
    assert _join(out) == text
    assert [o["word"] for o in out] == list(text)
    assert [o["confidence"] for o in out] == [0.01 * (i + 1) for i in range(len(text))]


def test_spurious_notation_token_is_dropped_and_char_interpolated():
    # 표기 차이로 본문에 없는 토큰(X)은 버리고, 그 자리 본문 글자(B)는 인접 토큰 시각으로 보간
    text = "ABC"
    toks = [_seg("A", 1.0, 1.2), _seg("X", 1.2, 1.4, conf=0.9), _seg("C", 1.4, 1.6)]
    out = _full_coverage_words(text, toks)
    assert _join(out) == text
    assert _monotonic(out)
    b = [o for o in out if o["word"] == "B"][0]
    assert b["confidence"] is None  # 보간 글자
    assert [o["word"] for o in out] == ["A", "B", "C"]


def test_multichar_token_covers_multiple_body_chars():
    text = "繰り返し 映す"
    toks = [_seg("繰り返し", 5.0, 5.8, conf=0.02), _seg("映す", 6.0, 6.4, conf=0.03)]
    out = _full_coverage_words(text, toks)
    assert _join(out) == text
    assert _monotonic(out)
    # 공백만 보간, 멀티글자 토큰은 그대로 본문 부분문자열로 방출
    assert out[0]["word"] == "繰り返し" and out[0]["confidence"] == 0.02


def test_empty_tokens_returns_empty():
    assert _full_coverage_words("なにか", []) == []
    assert _full_coverage_words("なにか", [_seg("", 0.0, 0.1)]) == []


def test_leading_and_trailing_gap_chars():
    text = " AB "  # 앞뒤 공백
    toks = [_seg("A", 2.0, 2.1), _seg("B", 2.1, 2.2)]
    out = _full_coverage_words(text, toks)
    assert _join(out) == text
    assert _monotonic(out)
    # 라인 경계를 안 주면 예전대로 안쪽 앵커에 붙은 길이 0
    assert out[0]["word"] == " " and out[0]["confidence"] is None
    assert out[-1]["word"] == " " and out[-1]["confidence"] is None
    assert out[0]["start"] == out[0]["end"] == 2.0
    assert out[-1]["start"] == out[-1]["end"] == 2.2


# --- 길이 0 blip 제거: 미커버 구간을 앞뒤 앵커 사이에 글자 수 비례 분배 -----------


def test_uncovered_run_is_spread_proportionally_not_zero_length():
    # 독음 역매핑 실패 재현: 가운데 3글자가 타이밍을 못 받는다. 예전에는 셋 다 prev_end에
    # 길이 0으로 찍혀 화면에서 한꺼번에 점등했다 (실측: 원문 글자 55~69%가 길이 0,
    # 3글자 이상 동시 시작 38~59%). 이제 앞뒤 앵커 사이에 균등 분배된다.
    text = "あいうえお"
    toks = [_seg("あ", 0.0, 1.0), _seg("お", 4.0, 5.0)]
    out = _full_coverage_words(text, toks)

    assert _join(out) == text  # 계약 유지
    assert _monotonic(out)
    mid = out[1:4]
    assert [o["start"] for o in mid] == [1.0, 2.0, 3.0]
    assert [o["end"] for o in mid] == [2.0, 3.0, 4.0]
    assert len({o["start"] for o in mid}) == 3  # 동시 시작 0건
    assert all(o["confidence"] is None for o in mid)  # 보간 글자는 conf 없음


def test_no_zero_length_blip_in_sparsely_mapped_line():
    # 실사용 형태: 토큰이 드문드문만 붙은 라인 — 길이 0 글자와 3글자 동시 시작이 사라진다
    text = "誰も知らない世界の果てまで"
    toks = [_seg("誰", 10.0, 10.3), _seg("世", 12.4, 12.7), _seg("で", 14.0, 14.3)]
    out = _full_coverage_words(text, toks, 9.8, 14.5)

    assert _join(out) == text
    assert _monotonic(out)
    assert all(o["end"] > o["start"] for o in out)  # 길이 0 글자 없음
    starts = [o["start"] for o in out]
    assert len(set(starts)) == len(starts)  # 동시 시작 없음


def test_outer_runs_borrow_from_line_bounds_within_per_char_cap():
    text = "ABCD"
    toks = [_seg("B", 2.0, 2.5), _seg("C", 2.5, 3.0)]
    out = _full_coverage_words(text, toks, 1.0, 5.0)

    assert _join(out) == text
    assert _monotonic(out)
    # 선두 A: 라인 시작(1.0)까지 빌리되 글자당 0.3s 상한 → 1.7~2.0
    assert out[0]["start"] == 1.7 and out[0]["end"] == 2.0
    # 말미 D: 라인 끝(5.0)까지 빌리되 상한 → 3.0~3.3 (라인 끝 5.0을 통째로 점등하지 않는다)
    assert out[3]["start"] == 3.0 and out[3]["end"] == 3.3


def test_outer_run_clipped_by_nearby_line_bound():
    # 라인 경계가 글자당 상한보다 가까우면 경계가 이긴다 (라인 밖으로 안 나간다)
    text = "AB"
    toks = [_seg("A", 2.0, 2.2)]
    out = _full_coverage_words(text, toks, 1.95, 2.3)
    assert _join(out) == text
    assert out[1]["start"] == 2.2 and out[1]["end"] == 2.3


def test_backward_tokens_collapse_gap_to_zero_length():
    # 원 토큰이 비단조면(뒤 앵커가 앞 앵커보다 이름) 구간을 앞 앵커에 붙인 길이 0으로 접는다
    # — 예전 동작과 동일. 없던 역행을 새로 만들지 않는다.
    text = "ABC"
    toks = [_seg("A", 5.0, 6.0), _seg("C", 1.0, 2.0)]
    out = _full_coverage_words(text, toks)
    assert _join(out) == text
    assert out[1]["start"] == 6.0 and out[1]["end"] == 6.0


def test_all_tokens_spurious_still_covers_body():
    # 토큰이 전부 본문에 없는 표기(전부 스퓨리어스) → 라인 경계에 균등 분배, join 계약 유지
    text = "あいう"
    toks = [_seg("x", 1.0, 1.2), _seg("y", 1.2, 1.4)]
    out = _full_coverage_words(text, toks, 0.0, 3.0)
    assert _join(out) == text
    assert _monotonic(out)
    assert out[0]["start"] == 0.0 and out[-1]["end"] == 3.0
