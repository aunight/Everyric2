"""오디오 심판(라인별 후보 독음 채점) + 「들은 것」 greedy 디코딩 회귀 테스트.

검증 전략: GPU도 실오디오도 쓸 수 없으므로 **후보 A가 정답인 emission을 직접 구성**해
기전을 검증한다. emission은 실제 모델과 같은 규약(정규화된 log 확률, shape (1, T, V))을
지키는 log_softmax 텐서이고, vocab은 실제 MMS 한국어 어댑터 vocab을 그대로 기록한 fixture
(`fixtures/mms_kor_vocab.json`, 1330개)다 — 심판 로직 자체는 모킹하지 않고 실제
`_align_cjk` 경로를 통째로 돌린다(`_ctc_log_emission`만 합성 텐서로 대체).

실오디오 채택 판정(私→와타시 등)은 서버에서 별도로 측정한다. 여기서 고정하는 것은 기전이다:
정답 후보가 이기는가, 마진 미달이면 안 바뀌는가, 후보가 1개면 아예 안 돌는가, 창이 짧은
라인은 건너뛰는가, 심판이 꺼져 있으면 기존 동작과 동일한가.
"""
import json
import math
from pathlib import Path

import torch
from torchaudio.functional import forced_align, merge_tokens

from everyric2.alignment.ctc_engine import (
    CTCEngine,
    _candidate_tokens,
    _greedy_text,
    _min_ctc_frames,
    _resolve_token_char,
)
from everyric2.config.settings import AlignmentSettings
from everyric2.inference.prompt import LyricLine

VOCAB = json.loads(
    (Path(__file__).parent / "fixtures" / "mms_kor_vocab.json").read_text(encoding="utf-8")
)
ID_TO_TOKEN = {tid: tok for tok, tid in VOCAB.items()}

# 실측된 MMS 어댑터 blank id (eng/kor/jpn 모두 pad_token_id=0)
BLANK_ID = 0

# 실제 오독 사례를 그대로 쓴다 (pron_style 실측: 사람 와타시 / 기계 와타쿠시)
DEFAULT_PRON = "와타쿠시와 아루쿠"
TRUE_PRON = "와타시와 아루쿠"


class _FakeTokenizer:
    def __init__(self, vocab):
        self._vocab = vocab
        self.pad_token_id = BLANK_ID

    def get_vocab(self):
        return self._vocab


class _FakeProcessor:
    def __init__(self, vocab):
        self.tokenizer = _FakeTokenizer(vocab)


def _ids(text: str) -> list[int]:
    return [
        VOCAB[_resolve_token_char(ch, VOCAB)]
        for ch in text
        if _resolve_token_char(ch, VOCAB) is not None
    ]


def _emission_favoring(token_ids: list[int], frames_per_token: int = 4, peak: float = 8.0):
    """지정한 토큰 열을 순서대로 강하게 지지하는 log_softmax emission (1, T, V)."""
    total = len(token_ids) * frames_per_token
    logits = torch.full((1, total, len(VOCAB)), -8.0)
    for i, tid in enumerate(token_ids):
        lo = i * frames_per_token
        logits[0, lo : lo + frames_per_token, tid] = peak
    return torch.log_softmax(logits, dim=-1)


def _engine(**kwargs):
    return CTCEngine(AlignmentSettings(star_tokens=False, align_chunk_sec=0.0, **kwargs))


def _run(engine, lines, emission):
    engine._processor = _FakeProcessor(VOCAB)
    engine._ctc_log_emission = lambda waveform, device: emission  # noqa: ARG005
    engine._device = torch.device("cpu")
    waveform = torch.zeros(emission.shape[1] * 320)  # 20ms 프레임 ≈ 320 샘플/프레임
    return waveform, engine


def _align(engine, lines, emission, **kwargs):
    waveform, engine = _run(engine, lines, emission)
    return engine._align_cjk(waveform, lines, "ko", None, **kwargs)


def _line(text: str) -> LyricLine:
    return LyricLine(text=text, line_number=1)


# --------------------------------------------------------------------------
# 1~2) 오디오가 정답 후보를 고른다 (두 방향 모두)
# --------------------------------------------------------------------------


def test_audio_picks_the_challenger_when_the_audio_says_so():
    # 후보 B(와타시)가 정답인 emission → 기본값(와타쿠시)이 B로 교체돼야 한다
    emission = _emission_favoring(_ids(TRUE_PRON) + [VOCAB["|"]])
    engine = _engine()
    results = _align(
        engine,
        [_line(DEFAULT_PRON)],
        emission,
        line_candidates={0: [DEFAULT_PRON, TRUE_PRON]},
    )

    assert results[0].text == TRUE_PRON, "오디오가 지지하는 후보로 교체되지 않았다"
    decisions = engine.get_last_referee_decisions()
    assert len(decisions) == 1
    d = decisions[0]
    assert d["default"] == DEFAULT_PRON and d["chosen"] == TRUE_PRON
    assert d["gain"] > d["margin"] > 0
    # 이긴 후보의 슬라이스 정렬 결과가 그대로 음절 스팬이 된다 (3패스 없음)
    assert "".join(w.word for w in results[0].word_segments) == TRUE_PRON.replace(" ", "")


def test_audio_keeps_the_default_when_the_audio_says_so():
    # 반대 방향: 기본값(와타쿠시)이 정답인 emission → 후보가 있어도 기본값 유지
    emission = _emission_favoring(_ids(DEFAULT_PRON) + [VOCAB["|"]])
    engine = _engine()
    results = _align(
        engine,
        [_line(DEFAULT_PRON)],
        emission,
        line_candidates={0: [DEFAULT_PRON, TRUE_PRON]},
    )

    assert results[0].text == DEFAULT_PRON
    d = engine.get_last_referee_decisions()[0]
    assert d["chosen"] == DEFAULT_PRON
    assert d["gain"] < 0, "정답이 기본값인데 도전자가 더 높은 점수를 받았다"


def _per_token_and_sum(window, text):
    ids = _candidate_tokens(text, VOCAB)[1]
    aligned, scores = forced_align(
        window, torch.tensor([ids], dtype=torch.int32), blank=BLANK_ID
    )
    spans = merge_tokens(aligned[0], scores[0], blank=BLANK_ID)
    total = sum(float(s.score) for s in spans)
    return total / len(spans), total


def test_normalisation_is_what_stops_short_candidates_from_always_winning():
    # 균일 emission(어느 토큰도 지지하지 않는 오디오)에서 두 후보의 **토큰당 평균**은 같다 —
    # 즉 오디오가 아무 정보를 주지 않으면 심판도 아무 선호를 갖지 않는다. 반면 **합**으로
    # 비교하면 토큰이 적은 후보가 압도적으로 이긴다(음수의 합이라 항이 적을수록 크다).
    window = torch.log_softmax(torch.zeros((1, 40, len(VOCAB))), dim=-1)
    long_avg, long_sum = _per_token_and_sum(window, TRUE_PRON)
    short_avg, short_sum = _per_token_and_sum(window, "와타")

    assert math.isclose(long_avg, short_avg, rel_tol=1e-6), (long_avg, short_avg)
    assert short_sum > long_sum + 10, (short_sum, long_sum)


def test_flat_emission_never_switches_the_reading():
    # 위 성질의 행동판: 오디오가 무정보면 훨씬 짧은 도전자도 기본값을 못 이긴다
    emission = torch.log_softmax(torch.zeros((1, 60, len(VOCAB))), dim=-1)
    engine = _engine()
    results = _align(
        engine, [_line(DEFAULT_PRON)], emission, line_candidates={0: [DEFAULT_PRON, "와타"]}
    )
    assert results[0].text == DEFAULT_PRON
    d = engine.get_last_referee_decisions()[0]
    assert abs(d["gain"]) < d["margin"], d


# --------------------------------------------------------------------------
# 3) 마진 미달이면 교체하지 않는다
# --------------------------------------------------------------------------


def test_margin_not_met_keeps_the_default():
    # 두 후보가 거의 같은 점수를 받는 emission(약한 peak, 아무 토큰도 강하게 지지하지 않음)
    # → 마진을 크게 걸면 기본값이 유지되어야 한다
    emission = _emission_favoring(_ids(TRUE_PRON) + [VOCAB["|"]], peak=0.05)
    engine = _engine()
    results = _align(
        engine,
        [_line(DEFAULT_PRON)],
        emission,
        line_candidates={0: [DEFAULT_PRON, TRUE_PRON]},
        referee_margins={0: 5.0},
    )
    assert results[0].text == DEFAULT_PRON
    d = engine.get_last_referee_decisions()[0]
    assert d["chosen"] == DEFAULT_PRON
    assert d["gain"] < d["margin"]

    # 같은 emission·같은 후보인데 마진만 0으로 낮추면 교체된다 → 판정을 가른 것은 마진뿐이다
    engine2 = _engine()
    switched = _align(
        engine2,
        [_line(DEFAULT_PRON)],
        emission,
        line_candidates={0: [DEFAULT_PRON, TRUE_PRON]},
        referee_margins={0: 0.0},
    )
    assert switched[0].text == TRUE_PRON


def test_human_margin_is_larger_than_the_deterministic_margin():
    # 사람이 쓴 발음은 기본값으로 두되 더 큰 마진을 요구한다 (근거는 worker 주석 참조)
    s = AlignmentSettings()
    assert s.pron_referee is True
    assert s.pron_referee_human_margin > s.pron_referee_margin > 0


# --------------------------------------------------------------------------
# 4) 후보가 1개면 심판이 아예 안 돈다 (호출 횟수로 확인)
# --------------------------------------------------------------------------


def test_single_candidate_never_scores(monkeypatch):
    import everyric2.alignment.ctc_engine as mod

    calls = []
    real = mod._score_tokens
    monkeypatch.setattr(
        mod, "_score_tokens", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    )

    emission = _emission_favoring(_ids(DEFAULT_PRON) + [VOCAB["|"]])
    engine = _engine()
    results = _align(
        engine,
        [_line(DEFAULT_PRON)],
        emission,
        line_candidates={0: [DEFAULT_PRON]},
    )
    assert calls == [], "후보 1개인데 채점이 돌았다 (비용 0이어야 한다)"
    assert engine.get_last_referee_decisions() == []
    assert results[0].text == DEFAULT_PRON

    # 중복만 든 후보 목록도 같다 — 중복 제거 후 1개
    engine2 = _engine()
    _align(
        engine2,
        [_line(DEFAULT_PRON)],
        emission,
        line_candidates={0: [DEFAULT_PRON, DEFAULT_PRON, ""]},
    )
    assert calls == []


def test_identical_token_sequences_are_scored_once(monkeypatch):
    # 문절 띄어쓰기만 다른 후보는 토큰열이 같아 점수도 같다 → 한 번만 채점한다
    import everyric2.alignment.ctc_engine as mod

    calls = []
    real = mod._score_tokens
    monkeypatch.setattr(
        mod, "_score_tokens", lambda *a, **k: (calls.append(1), real(*a, **k))[1]
    )
    spaced = "와타쿠시와 아루쿠"
    unspaced = "와타쿠시와아루쿠"
    assert _candidate_tokens(spaced, VOCAB)[1] == _candidate_tokens(unspaced, VOCAB)[1]

    emission = _emission_favoring(_ids(spaced) + [VOCAB["|"]])
    engine = _engine()
    results = _align(
        engine, [_line(spaced)], emission, line_candidates={0: [spaced, unspaced]}
    )
    assert len(calls) == 1
    assert results[0].text == spaced, "동점이면 기본값이 남아야 한다"


# --------------------------------------------------------------------------
# 5) 창이 짧은 라인은 건너뛴다
# --------------------------------------------------------------------------


def test_min_ctc_frames_counts_the_blank_between_repeats():
    assert _min_ctc_frames([5, 6, 7]) == 3
    assert _min_ctc_frames([5, 5, 6]) == 4  # 반복 사이 blank 1개
    assert _min_ctc_frames([]) == 0


def test_short_window_line_is_skipped():
    # 프레임이 후보 토큰 수보다 적은 창 — 그 후보는 채점 대상이 아니다.
    # 라인 전체를 1프레임/토큰으로 정렬시키면 기본값 창이 곧 기본값 토큰 수라서,
    # 기본값보다 긴 후보는 창에 들어가지 못한다.
    default_short = "와타"
    long_challenger = "와타시와 아루쿠"
    emission = _emission_favoring(_ids(default_short) + [VOCAB["|"]], frames_per_token=1)
    engine = _engine()
    results = _align(
        engine,
        [_line(default_short)],
        emission,
        line_candidates={0: [default_short, long_challenger]},
    )
    assert results[0].text == default_short
    d = engine.get_last_referee_decisions()[0]
    assert [s[0] for s in d["scores"]] == [default_short], (
        f"창({d['frames']}프레임)에 안 들어가는 후보가 채점됐다: {d['scores']}"
    )


def test_line_with_no_aligned_char_is_skipped():
    # 정렬된 글자가 0개인 라인(전부 vocab 밖 — kor 어댑터에 가나는 0개)은 프레임 창이
    # 없어 심판할 수 없다. 그 라인은 기존 보간 경로로 그대로 넘어간다.
    oov_line = "あいカナ"
    assert _candidate_tokens(oov_line, VOCAB)[1] == []
    emission = _emission_favoring(_ids(DEFAULT_PRON) + [VOCAB["|"]])
    engine = _engine()
    lines = [_line(DEFAULT_PRON), LyricLine(text=oov_line, line_number=2)]
    results = _align(engine, lines, emission, line_candidates={1: [oov_line, "와타시"]})
    assert engine.get_last_referee_decisions() == []
    assert results[1].text == oov_line


# --------------------------------------------------------------------------
# 6) 「들은 것」 greedy 디코딩
# --------------------------------------------------------------------------


def test_greedy_collapses_repeats_and_drops_blanks():
    ids = _ids("와타시")
    # 반복 프레임 + 사이사이 blank가 섞인 실제 CTC 출력 모양
    frames = [ids[0], ids[0], BLANK_ID, ids[1], BLANK_ID, BLANK_ID, ids[2], ids[2], ids[2]]
    assert _greedy_text(frames, BLANK_ID, ID_TO_TOKEN) == "와타시"


def test_greedy_keeps_a_repeated_token_separated_by_blank():
    a = VOCAB["나"]
    # blank로 갈라진 같은 토큰은 두 번 남아야 한다 (반복 축약이 blank를 무시하면 안 된다)
    assert _greedy_text([a, a, BLANK_ID, a, a], BLANK_ID, ID_TO_TOKEN) == "나나"
    # blank가 없으면 한 번으로 축약된다
    assert _greedy_text([a, a, a, a], BLANK_ID, ID_TO_TOKEN) == "나"


def test_heard_text_is_reported_per_line():
    # 두 라인을 각자 강하게 지지하는 emission → 라인별 창에서 각 라인 텍스트를 들어야 한다
    l1, l2 = "와타시", "아루쿠"
    emission = _emission_favoring(
        _ids(l1) + [VOCAB["|"]] + _ids(l2) + [VOCAB["|"]]
    )
    engine = _engine()
    _align(engine, [_line(l1), LyricLine(text=l2, line_number=2)], emission)
    heard = engine.get_last_heard_lines()
    assert heard == {0: l1, 1: l2}, heard


def test_heard_text_exposes_a_mismatch_with_the_aligned_text():
    # 정렬 텍스트와 「들은 것」의 불일치가 라인 품질 지표다 — 모델이 다른 걸 들으면 그게 보인다
    emission = _emission_favoring(_ids("아루쿠") + [VOCAB["|"]])
    engine = _engine()
    _align(engine, [_line("와타시")], emission)
    assert engine.get_last_heard_lines()[0] != "와타시"


# --------------------------------------------------------------------------
# 7) 심판이 꺼져 있으면(후보 미전달) 기존 동작과 완전히 동일
# --------------------------------------------------------------------------


def test_no_candidates_is_bit_identical_to_the_old_path():
    lines = [_line(DEFAULT_PRON), LyricLine(text="아루쿠", line_number=2)]
    emission = _emission_favoring(
        _ids(DEFAULT_PRON) + [VOCAB["|"]] + _ids("아루쿠") + [VOCAB["|"]]
    )

    a = _engine()
    ra = _align(a, lines, emission)
    b = _engine()
    rb = _align(b, lines, emission, line_candidates=None, referee_margins=None)

    assert [r.text for r in ra] == [r.text for r in rb] == [DEFAULT_PRON, "아루쿠"]
    for x, y in zip(ra, rb):
        assert math.isclose(x.start_time, y.start_time, abs_tol=1e-12)
        assert math.isclose(x.end_time, y.end_time, abs_tol=1e-12)
        assert [(w.word, w.start, w.end, w.confidence) for w in x.word_segments] == [
            (w.word, w.start, w.end, w.confidence) for w in y.word_segments
        ]
    assert [
        (w.word, w.start, w.end, w.confidence) for w in a._last_word_timestamps
    ] == [(w.word, w.start, w.end, w.confidence) for w in b._last_word_timestamps]
    assert b.get_last_referee_decisions() == []


def test_word_timestamp_order_is_line_order_even_when_a_line_is_replaced():
    # 교체된 라인의 글자 타임스탬프가 목록 끝으로 밀리면 표시·전사 순서가 깨진다
    lines = [_line(DEFAULT_PRON), LyricLine(text="아루쿠", line_number=2)]
    emission = _emission_favoring(
        _ids(TRUE_PRON) + [VOCAB["|"]] + _ids("아루쿠") + [VOCAB["|"]]
    )
    engine = _engine()
    results = _align(
        engine, lines, emission, line_candidates={0: [DEFAULT_PRON, TRUE_PRON]}
    )
    assert results[0].text == TRUE_PRON
    got = "".join(w.word for w in engine._last_word_timestamps)
    assert got == (TRUE_PRON + "아루쿠").replace(" ", ""), got
    starts = [w.start for w in engine._last_word_timestamps]
    assert starts == sorted(starts), "글자 타임스탬프가 시간순이 아니다"


def test_referee_record_is_cleared_between_alignments():
    # ko 정렬의 판정이 다음(ja) 정렬 뒤에 남아 있으면 워커가 엉뚱한 라인을 보고한다
    emission = _emission_favoring(_ids(TRUE_PRON) + [VOCAB["|"]])
    engine = _engine()
    _align(engine, [_line(DEFAULT_PRON)], emission, line_candidates={0: [DEFAULT_PRON, TRUE_PRON]})
    assert engine.get_last_referee_decisions()
    _align(engine, [_line(DEFAULT_PRON)], emission)
    assert engine.get_last_referee_decisions() == []
