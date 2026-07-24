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
    # 앞 공백은 첫 토큰 시각(next_start)으로, 뒤 공백은 마지막 토큰 end로 앵커
    assert out[0]["word"] == " " and out[0]["confidence"] is None
    assert out[-1]["word"] == " " and out[-1]["confidence"] is None
