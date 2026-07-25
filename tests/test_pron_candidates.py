"""후보 독음 생성기(pron_style.pronunciation_candidates) + 워커 배선 테스트.

no-mock: fugashi + unidic-lite / pykakasi 실제 분석 결과를 그대로 쓴다. 여기서 고정하는
것은 "오디오 심판이 고를 수 있는 서로 다른 독음이 실제로 생성되는가"다 — 어느 후보가 맞는지는
오디오가 고르므로(tests/test_pron_referee.py) 이 파일은 정답을 주장하지 않는다.

사례는 전부 실측 오독이다 (결정론 경로는 사람이 쓴 위키 발음 2,207줄 대비 82.1%이고 남은
불일치가 "어느 독음이 맞나"로 수렴한다): 私 와타시/와타쿠시, 三日月 미카즈키/밋카츠키,
数え事 카조에 고토(연탁)/코토, 何も 나니모/난모, 涙（シル） 아테지.
"""
import pytest

from everyric2.config.settings import AlignmentSettings
from everyric2.server.worker import _referee_candidates
from everyric2.text.ja_reading import (
    has_ruby,
    tokenize_reading_nbest,
    tokenize_reading_pykakasi,
)
from everyric2.text.pron_style import pronunciation_candidates, wiki_pronunciation


class _Line:
    def __init__(self, text):
        self.text = text


# ---------------------------------------------------------------------------
# 후보 목록의 계약
# ---------------------------------------------------------------------------


def test_first_candidate_is_exactly_the_deterministic_default():
    # [0]이 기본값이어야 심판이 "기본값을 마진 이상 이기는가"를 물을 수 있다
    for text in ("私は歩く", "三日月の夜", "何も言えない", "涙（シル）をこぼす"):
        assert pronunciation_candidates(text)[0] == wiki_pronunciation(text)


def test_candidates_are_distinct_and_non_empty():
    for text in ("私は歩く", "数え事をする", "今更止められない"):
        cands = pronunciation_candidates(text)
        assert len(cands) == len(set(cands)), cands
        assert all(c.strip() for c in cands)


def test_no_candidates_for_non_japanese_lines():
    # 심판을 돌릴 이유가 없는 라인은 빈 목록 → 비용 0
    for text in ("hello world", "오늘 밤", "", "123"):
        assert pronunciation_candidates(text) == []


def test_max_candidates_is_respected():
    assert len(pronunciation_candidates("三日月の夜", max_candidates=3)) == 3
    assert pronunciation_candidates("三日月の夜", max_candidates=0) == []


@pytest.mark.parametrize(
    "text,alternative",
    [
        # 사람 와타시 / 기계 와타쿠시 — MeCab N-best 대안 파스
        ("私は歩く", "와타시와 아루쿠"),
        # 사람 미카즈키 / 기계 미카츠키
        ("三日月の夜", "미카즈키노 요루"),
        # 사람 나니모 / 기계 난모
        ("何も言えない", "나니모 이에나이"),
        # 連濁: 사람 카조에 고토 / 기계 카조에 코토
        ("数え事をする", "카조에 고토오 스루"),
        # 조사 は의 표층 읽기 변종 (phonetic=False 축)
        ("私は歩く", "와타쿠시하 아루쿠"),
    ],
)
def test_known_misreadings_appear_as_candidates(text, alternative):
    cands = pronunciation_candidates(text)
    assert alternative in cands, f"{text}: {alternative} 가 후보에 없다 — {cands}"
    assert cands[0] != alternative, "이 사례의 기본값은 대안이 아니어야 한다(오독 사례)"


def test_ruby_line_offers_the_unadopted_reading():
    # 루비 채택은 순이득 +0.7p(개선 14줄 / 악화 6줄)에 그쳤다 — 그 6줄이 이 축이다
    text = "涙（シル）をこぼす"
    assert has_ruby(text)
    cands = pronunciation_candidates(text)
    assert cands[0] == "(시루)오 코보스"
    assert any(c.startswith("나미다") for c in cands), cands


def test_non_ruby_line_does_not_waste_a_candidate_on_the_ruby_axis():
    # 루비가 없으면 adopt_ruby 축은 기본값과 동일하다 — 중복 제거로 사라져야 한다
    text = "何も言えない"
    assert not has_ruby(text)
    cands = pronunciation_candidates(text)
    assert len(cands) == len(set(cands))


# ---------------------------------------------------------------------------
# 후보 생성의 재료 (ja_reading의 새 접근자)
# ---------------------------------------------------------------------------


def test_nbest_returns_alternative_parses_only():
    text = "私は三日月を見た"
    parses = tokenize_reading_nbest(text, n=8, phonetic=True)
    assert parses, "N-best 대안 파스를 못 얻었다"
    readings = {"".join(t.reading for t in p) for p in parses}
    assert len(readings) > 1
    # 오프셋·표면 계약은 1순위 파스와 동일해야 한다 (모라 정렬이 이 불변식에 걸려 있다)
    for parse in parses:
        assert "".join(t.surface for t in parse) == text
        for token in parse:
            assert text[token.start : token.end] == token.surface


def test_nbest_is_a_noop_when_disabled():
    assert tokenize_reading_nbest("私は歩く", n=1) == []
    assert tokenize_reading_nbest("", n=8) == []


def test_pykakasi_fallback_tokens_keep_the_offset_contract():
    text = "今更止められない"
    tokens = tokenize_reading_pykakasi(text)
    assert tokens
    assert "".join(t.surface for t in tokens) == text
    assert tokenize_reading_pykakasi("") == []


# ---------------------------------------------------------------------------
# 워커 배선 — 기본 후보와 라인별 마진
# ---------------------------------------------------------------------------


def _settings(**kw):
    # 심판은 실오디오 실측에서 해로웠으므로 기본값이 False다(근거는 AlignmentSettings 참조).
    # 이 파일은 기전 자체를 검증하므로 명시적으로 켠다 — 기본값에 기대면 안 된다.
    kw.setdefault("pron_referee", True)
    return AlignmentSettings(**kw)


def test_worker_puts_the_line_meta_pronunciation_first():
    lines = [_Line("私は歩く")]
    default = wiki_pronunciation("私は歩く")
    cands, margins = _referee_candidates(lines, [default], _settings())
    assert cands[0][0] == default
    assert len(cands[0]) > 1
    # 결정론 기본값 → 작은 마진
    assert margins[0] == _settings().pron_referee_margin


def test_worker_requires_a_larger_margin_for_a_human_written_pronunciation():
    # 사람이 쓴 발음은 결정론 출력과 다르다는 사실만으로 식별된다 (별도 플래그 없음).
    # 심판 대상에서 빼지 않고 마진만 크게 요구한다 — 사람도 후리가나를 놓치는 줄이 있다.
    lines = [_Line("私は歩く")]
    human = "와타시와 아루쿠"
    assert human != wiki_pronunciation("私は歩く")
    cands, margins = _referee_candidates(lines, [human], _settings())
    assert cands[0][0] == human, "사람 발음이 기본값이어야 한다"
    assert wiki_pronunciation("私は歩く") in cands[0], "결정론 독음도 후보로 남아야 한다"
    s = _settings()
    assert margins[0] == s.pron_referee_human_margin > s.pron_referee_margin


def test_worker_skips_lines_without_alternatives():
    # 발음이 없는 라인, 그리고 대안이 없는 라인은 심판 대상이 아니다 → 비용 0
    lines = [_Line("私は歩く"), _Line("hello"), _Line("오늘")]
    cands, margins = _referee_candidates(lines, ["와타쿠시와 아루쿠", "", ""], _settings())
    assert set(cands) == {0}
    assert set(margins) == {0}


def test_worker_respects_the_candidate_cap():
    lines = [_Line("三日月の夜")]
    cands, _ = _referee_candidates(
        lines, [wiki_pronunciation("三日月の夜")], _settings(pron_referee_max_candidates=3)
    )
    assert len(cands[0]) == 3


# ---------------------------------------------------------------------------
# 워커 배선 — 심판이 이긴 후보가 역매핑·표시·디버그에 실제로 반영되는가
# ---------------------------------------------------------------------------

_JA = "私は歩く"
_DEFAULT = wiki_pronunciation(_JA)
_WINNER = "와타시와 아루쿠"


class _FakeEngine:
    """align 호출 인자를 기록하고, 심판이 이긴 후보를 text로 돌려주는 엔진 대역.

    실제 채점은 tests/test_pron_referee.py가 합성 emission으로 검증한다 — 여기서는
    "엔진이 고른 독음이 워커의 역매핑·표시·디버그까지 흘러가는가"만 본다.
    """

    def __init__(self, winner: str | None):
        self.winner = winner
        self.kwargs = None
        self._last_referee = []
        self._last_heard = {}

    def align(self, audio, lyrics, language=None, **kwargs):
        from everyric2.inference.prompt import SyncResult, WordSegment

        self.kwargs = kwargs
        results = []
        for i, ln in enumerate(lyrics):
            text = self.winner if (i == 0 and self.winner) else ln.text
            syllables = [ch for ch in text if ch != " "]
            step = 0.2
            results.append(
                SyncResult(
                    line_number=ln.line_number,
                    text=text,
                    start_time=0.0,
                    end_time=step * len(syllables),
                    word_segments=[
                        WordSegment(word=ch, start=step * k, end=step * (k + 1), confidence=0.5)
                        for k, ch in enumerate(syllables)
                    ],
                )
            )
        if self.winner:
            self._last_referee = [
                {
                    "line": 0,
                    "default": _DEFAULT,
                    "chosen": self.winner,
                    "margin": 0.15,
                    "gain": 0.42,
                    "frames": 60,
                    "scores": [[_DEFAULT, -3.1], [self.winner, -2.68]],
                }
            ]
        self._last_heard = {0: "와타시와 아루쿠"}
        return results


def _pron_align(winner):
    from everyric2.inference.prompt import LyricLine
    from everyric2.server.worker import _align_with_pronunciation, _pron_by_text

    engine = _FakeEngine(winner)
    lines = [LyricLine(text=_JA, line_number=1)]
    by_text = _pron_by_text([{"text": _JA, "pronunciation": _DEFAULT, "translation": "나는 걷는다"}])
    results, pron_data = _align_with_pronunciation(
        engine, object(), lines, by_text, _settings()
    )
    return engine, results, pron_data


def test_worker_passes_candidates_to_the_engine():
    engine, _, _ = _pron_align(None)
    assert engine.kwargs["line_candidates"][0][0] == _DEFAULT
    assert _WINNER in engine.kwargs["line_candidates"][0]
    assert engine.kwargs["referee_margins"][0] == _settings().pron_referee_margin


def test_worker_adopts_the_winning_reading_for_display_and_backmapping():
    _, results, pron_data = _pron_align(_WINNER)
    # 표시 발음이 이긴 후보로 바뀐다 (私 → 와타시)
    assert pron_data[0]["pronunciation"] == _WINNER
    # 원문 라인은 그대로고, 음절 스팬은 이긴 후보의 음절 수만큼 잡힌다
    assert results[0].text == _JA
    assert pron_data[0]["pron_segments"]
    assert len(pron_data[0]["pron_segments"]) == len(_WINNER.replace(" ", ""))


def test_worker_carries_heard_text_and_referee_reasoning_into_debug_meta():
    # 실오디오 검증에서 후보별 점수를 못 보면 판정을 되짚을 수 없다 — 반드시 실려야 한다
    _, _, pron_data = _pron_align(_WINNER)
    assert pron_data[0]["heard"] == "와타시와 아루쿠"
    ref = pron_data[0]["referee"]
    assert ref["default"] == _DEFAULT and ref["chosen"] == _WINNER
    assert ref["scores"] == [[_DEFAULT, -3.1], [_WINNER, -2.68]]
    assert "line" not in ref  # 라인 번호는 세그먼트 자체가 들고 있다


def test_line_meta_remerge_does_not_revert_a_referee_decision():
    # 캐시 재사용·늦은 메타 병합 경로가 심판 판정을 조용히 되돌리면, 표시 발음과
    # pron_segments(이긴 후보의 음절 스팬)의 음절 수가 어긋난다.
    from everyric2.server.worker import merge_line_meta

    switched = {
        "text": _JA,
        "pronunciation": _WINNER,
        "pron_segments": [{"text": "와", "start": 0.0, "end": 0.2}],
        "debug": {"referee": {"default": _DEFAULT, "chosen": _WINNER}},
    }
    kept = {
        "text": _JA,
        "pronunciation": _DEFAULT,
        "debug": {"referee": {"default": _DEFAULT, "chosen": _DEFAULT}},
    }
    plain = {"text": _JA}
    meta = [{"text": _JA, "pronunciation": _DEFAULT, "translation": "나는 걷는다"}]

    merge_line_meta([switched, kept, plain], meta)
    assert switched["pronunciation"] == _WINNER, "심판 판정이 재병합으로 되돌아갔다"
    assert switched["translation"] == "나는 걷는다", "번역 병합은 그대로여야 한다"
    # 심판이 기본값을 유지한 라인과 심판이 안 돈 라인은 예전대로 병합된다
    assert kept["pronunciation"] == _DEFAULT
    assert plain["pronunciation"] == _DEFAULT


def test_worker_omits_candidates_when_the_referee_is_off():
    from everyric2.inference.prompt import LyricLine
    from everyric2.server.worker import _align_with_pronunciation, _pron_by_text

    engine = _FakeEngine(None)
    lines = [LyricLine(text=_JA, line_number=1)]
    by_text = _pron_by_text([{"text": _JA, "pronunciation": _DEFAULT}])
    _align_with_pronunciation(engine, object(), lines, by_text, _settings(pron_referee=False))
    assert engine.kwargs["line_candidates"] is None
    assert engine.kwargs["referee_margins"] is None

    # align_settings를 아예 주지 않는 기존 호출부도 그대로 동작해야 한다
    engine2 = _FakeEngine(None)
    _align_with_pronunciation(engine2, object(), lines, by_text)
    assert engine2.kwargs["line_candidates"] is None
