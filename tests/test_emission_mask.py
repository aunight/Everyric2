"""자막 앵커 금지 구간 → emission 마스킹 기전 회귀 테스트.

검증 전략은 `test_pron_referee.py`와 같다: GPU도 실오디오도 쓸 수 없으므로 **사고를 재현하는
emission을 직접 구성**해 기전을 검증한다. emission은 실제 모델과 같은 규약(정규화된 log 확률,
shape (1, T, V))을 지키고 vocab은 실제 MMS 한국어 어댑터 fixture이며, `_align_cjk` 경로를
통째로 돌린다(`_ctc_log_emission`만 합성 텐서로 대체).

**균일 바닥 emission이 사고의 핵심이다.** star 열의 점수는 log(1.0)=0이고 실제 토큰은 전부
음수라, 토큰 점수가 전 구간 균일하면 DP에게는 「글자를 앞으로 몰고 뒤를 star로 비우기」가
최적해가 된다(zyRt-nBM3dY 실측 서명: star_spans=[23.8, 56.86] + 앞 12줄 압축 + 33초 공백).
아래 첫 테스트가 그 상황을 그대로 만들어 앵커가 그것을 되돌리는지 본다.

못박는 것: 금지 구간에 토큰이 배출되지 않는가, 실행가능성 미달이면 마스킹을 포기하는가,
손실 상한을 넘으면 1패스가 유지되는가, NaN이 생기지 않는가, 앵커가 없으면 기존 동작과
완전히 동일한가, 채택된 경로의 신뢰도가 마스크 바닥값에 오염되지 않는가.
"""

import json
import math
from pathlib import Path

import torch

from everyric2.alignment.ctc_engine import CTCEngine, _resolve_token_char
from everyric2.config.settings import AlignmentSettings
from everyric2.inference.prompt import LyricLine

VOCAB = json.loads(
    (Path(__file__).parent / "fixtures" / "mms_kor_vocab.json").read_text(encoding="utf-8")
)
BLANK_ID = 0

# 20ms 프레임 규약: 프레임 1개 = 320 샘플 @16kHz
SAMPLES_PER_FRAME = 320
FRAMES = 500  # = 10.0초
SEC_PER_FRAME = 0.02

LINES = ["아침이 오면", "너를 만나러", "달려갈 거야"]


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


def _flat_emission(frames: int = FRAMES):
    """전 구간 균일 바닥 — 합성보컬 posterior를 모사한다 (배치가 무차별해진다)."""
    return torch.log_softmax(torch.zeros((1, frames, len(VOCAB))), dim=-1)


def _peaky_emission(texts: list[str], frames_per_token: int = 4, frames: int = FRAMES):
    """주어진 토큰 열을 **앞쪽 프레임에서** 강하게 지지하는 emission."""
    logits = torch.full((1, frames, len(VOCAB)), -8.0)
    at = 0
    for text in texts:
        for tid in _ids(text):
            logits[0, at : at + frames_per_token, tid] = 8.0
            at += frames_per_token
    return torch.log_softmax(logits, dim=-1), at


def _engine(star: bool = True, **kwargs):
    return CTCEngine(AlignmentSettings(star_tokens=star, align_chunk_sec=0.0, **kwargs))


def _align(engine, texts, emission, **kwargs):
    engine._processor = _FakeProcessor(VOCAB)
    engine._ctc_log_emission = lambda waveform, device: emission  # noqa: ARG005
    engine._device = torch.device("cpu")
    waveform = torch.zeros(emission.shape[1] * SAMPLES_PER_FRAME)
    lines = [LyricLine(text=t, line_number=i + 1) for i, t in enumerate(texts)]
    return engine._align_cjk(waveform, lines, "ko", None, **kwargs)


def _all_chars(results):
    return [w for r in results if r.word_segments for w in r.word_segments]


# --------------------------------------------------------------------------
# 1) 금지 구간에서는 실제 토큰이 배출되지 않는다 (사고 재현 → 교정)
# --------------------------------------------------------------------------


def test_uniform_emission_crams_lines_forward_without_anchors():
    """앵커 없이는 사고가 그대로 재현된다 — 이 테스트가 아래 교정의 대조군이다."""
    results = _align(_engine(), LINES, _flat_emission())
    chars = _all_chars(results)
    assert chars, "정렬된 글자가 없다"
    # 균일 바닥에서 DP는 토큰 프레임을 최소화해 전부 앞으로 몰고 뒤를 star로 비운다
    assert chars[-1].end < 1.0, f"사고 서명이 재현되지 않았다 (마지막 글자 {chars[-1].end:.2f}s)"


def test_forbidden_span_pushes_every_character_out_of_it():
    emission = _flat_emission()
    engine = _engine()
    results = _align(engine, LINES, emission, forbidden_spans=[(0.0, 6.0)])

    chars = _all_chars(results)
    assert len(chars) == sum(len(_ids(t)) for t in LINES)
    # 경계 프레임은 ceil/floor로 잘라 두므로 6.0s 직전 프레임 한 개까지는 허용된다
    assert all(w.start >= 6.0 - SEC_PER_FRAME for w in chars), (
        f"금지 구간에 글자가 남았다: {[(w.word, round(w.start, 2)) for w in chars][:5]}"
    )
    d = engine.get_last_caption_anchor()
    assert d["adopted"] is True
    # 진짜 간주를 금지한 경우의 포기액은 0에 가깝다 (토큰이 갈 곳이 어차피 동등하다)
    assert d["loss"] == 0.0
    assert d["frames"] == 300 and d["spans"] == [[0.0, 6.0]]


def test_lines_can_still_hide_in_the_slack_before_the_span():
    """**이 기전의 한계다.** 금지 구간 앞에 남은 여유에는 여전히 줄이 들어갈 수 있다.

    실제 앵커의 금지 구간은 앞 앵커의 자막 표시 종료 + margin부터 시작하므로, 「자막이
    사라진 시점 ~ 금지 시작」 사이에 항상 여유가 남는다. 사고 곡에서는 압축이 8.7초에
    시작하고 앞 앵커의 자막이 9.40초에 끝나므로 (margin 1.0에서) 8.7~10.4초가 열려 있다.
    금지 구간은 압축의 **동기**를 없애지만(8줄 분량이 들어갈 자리가 사라진다) 앞 여유에
    들어가는 한두 줄은 그대로 남는다 — margin을 줄이면 더 닫히고 자막 오차에 더 민감해진다.
    이 절충을 눈에 보이게 못박아 둔다.
    """
    engine = _engine()
    results = _align(engine, LINES, _flat_emission(), forbidden_spans=[(0.4, 6.0)])
    starts = [r.start_time for r in results]
    assert starts[0] < 0.4 and starts[-1] >= 6.0
    # 여유(0.4s = 20프레임)에 들어갈 만큼만 남고 나머지는 금지 구간 뒤로 밀린다
    assert sum(1 for s in starts if s < 0.4) < len(LINES)


def test_star_channel_still_traverses_the_forbidden_span():
    # star는 금지 프레임에서도 열려 있어야 한다 — 간주를 흡수하는 것이 star의 일이다
    engine = _engine(star=True)
    _align(engine, LINES, _flat_emission(), forbidden_spans=[(0.0, 6.0)])
    assert engine.get_last_caption_anchor()["adopted"] is True
    swallowed = engine._last_star_spans
    assert any(s <= 0.5 and e >= 5.5 for s, e in swallowed), (
        f"star가 금지 구간을 덮지 않았다: {swallowed}"
    )


def test_masking_works_without_star_tokens_too():
    engine = _engine(star=False)
    results = _align(engine, LINES, _flat_emission(), forbidden_spans=[(0.0, 6.0)])
    assert engine.get_last_caption_anchor()["adopted"] is True
    assert all(w.start >= 6.0 - SEC_PER_FRAME for w in _all_chars(results))


# --------------------------------------------------------------------------
# 2) 채택 판정 — 손실이 크면 1패스를 유지한다
# --------------------------------------------------------------------------


def test_forbidding_real_singing_is_rejected_and_first_pass_survives():
    """앵커가 틀려 실제 가창을 금지하면 포기액이 폭발한다 → 마스킹을 버린다."""
    emission, used = _peaky_emission(LINES)
    sung_end = used * SEC_PER_FRAME
    baseline = _align(_engine(), LINES, emission)
    engine = _engine()
    results = _align(engine, LINES, emission, forbidden_spans=[(0.0, sung_end + 1.0)])

    d = engine.get_last_caption_anchor()
    assert d["adopted"] is False
    assert d["loss"] > d["max_loss"], f"봉우리를 금지했는데 포기액이 작다: {d}"
    # 1패스가 **그대로** 남아야 한다 (되돌릴 길이 없으므로 값까지 동일해야 한다)
    assert [(r.start_time, r.end_time) for r in results] == [
        (r.start_time, r.end_time) for r in baseline
    ]
    assert [w.start for w in _all_chars(results)] == [w.start for w in _all_chars(baseline)]


def test_loss_ceiling_is_what_decides():
    """같은 입력에서 상한만 올리면 채택된다 — 판정의 손잡이가 이 값임을 못박는다."""
    emission, used = _peaky_emission(LINES)
    spans = [(0.0, used * SEC_PER_FRAME + 1.0)]
    strict = _engine()
    _align(strict, LINES, emission, forbidden_spans=spans)
    loose = _engine(caption_anchor_max_token_loss=1e6)
    _align(loose, LINES, emission, forbidden_spans=spans)
    assert strict.get_last_caption_anchor()["adopted"] is False
    assert loose.get_last_caption_anchor()["adopted"] is True


def test_rescoring_uses_the_original_emission_not_the_mask():
    """두 경로의 점수는 마스킹 전 emission에서 재채점된다 — 목적함수가 같아야 비교가 성립한다.

    1패스는 원본 emission의 Viterbi 최적해이고 2패스는 같은 토큰열의 다른 경로이므로,
    경로 총점은 **항상** 1패스 >= 2패스다. 마스크 바닥값(-1e4)이 점수에 새면 이 관계가
    깨지고(2패스가 -1e6 규모로 찍힌다) 판정이 무의미해진다.
    """
    emission, _ = _peaky_emission(LINES)
    engine = _engine()
    _align(engine, LINES, emission, forbidden_spans=[(2.0, 6.0)])
    d = engine.get_last_caption_anchor()
    base, masked = d["path_score"]
    assert base >= masked
    # 마스크 바닥값이 섞였다면 이 규모가 될 수 없다 (프레임 500개 × -1e4 = -5e6)
    assert masked > -1e5
    # 채택 판정은 경로 총점이 아니라 글자별 최고 지지도로 한다 (star 커버리지에 지배되지 않는다)
    b_sup, n_sup = d["support"]
    assert b_sup >= n_sup and d["loss"] == round(b_sup - n_sup, 4)


def test_adopted_path_confidences_come_from_the_original_emission():
    engine = _engine()
    results = _align(engine, LINES, _flat_emission(), forbidden_spans=[(0.4, 6.0)])
    assert engine.get_last_caption_anchor()["adopted"] is True
    confs = [w.confidence for w in _all_chars(results)]
    assert confs and all(c is not None and c > 0.0 for c in confs), (
        f"신뢰도가 마스크 바닥값에 오염됐다: {confs[:5]}"
    )


# --------------------------------------------------------------------------
# 3) 실행가능성 — 못 하면 조용히가 아니라 기록을 남기고 포기한다
# --------------------------------------------------------------------------


def test_infeasible_mask_is_abandoned_not_forced():
    emission = _flat_emission()
    baseline = _align(_engine(), LINES, emission)
    engine = _engine()
    results = _align(engine, LINES, emission, forbidden_spans=[(0.1, 9.9)])

    d = engine.get_last_caption_anchor()
    assert d["skipped"] == "infeasible"
    assert "adopted" not in d
    assert d["free_frames"] < d["need_frames"]
    assert [w.start for w in _all_chars(results)] == [w.start for w in _all_chars(baseline)]


def test_span_thinner_than_a_frame_is_a_noop():
    engine = _engine()
    _align(engine, LINES, _flat_emission(), forbidden_spans=[(1.0, 1.005)])
    assert engine.get_last_caption_anchor()["skipped"] == "no_constraint"


# --------------------------------------------------------------------------
# 4) NaN 안전성과 무앵커 항등
# --------------------------------------------------------------------------


def test_no_nan_anywhere_in_the_masked_path():
    engine = _engine()
    results = _align(engine, LINES, _flat_emission(), forbidden_spans=[(0.4, 6.0)])
    for w in _all_chars(results):
        assert math.isfinite(w.start) and math.isfinite(w.end)
        assert w.confidence is not None and math.isfinite(w.confidence)
    d = engine.get_last_caption_anchor()
    assert all(math.isfinite(v) for v in d["support"])
    assert all(math.isfinite(v) for v in d["path_score"])
    assert math.isfinite(d["loss"])
    for r in results:
        assert r.start_time is not None and math.isfinite(r.start_time)


def test_without_anchors_the_engine_reports_nothing_and_behaves_identically():
    emission = _flat_emission()
    plain = _engine()
    plain_results = _align(plain, LINES, emission)
    assert plain.get_last_caption_anchor() is None

    # 빈 목록도 «앵커 없음»이다 (계획이 게이트에 걸린 경우가 그렇게 들어온다)
    empty = _engine()
    empty_results = _align(empty, LINES, emission, forbidden_spans=[])
    assert empty.get_last_caption_anchor() is None
    assert [w.start for w in _all_chars(empty_results)] == [
        w.start for w in _all_chars(plain_results)
    ]


# --------------------------------------------------------------------------
# 5) 양성 제약 — 「여기 있을 수 없다」가 함의하지 못하는 「여기 있어야 한다」
# --------------------------------------------------------------------------
#
# 실측(zyRt-nBM3dY, 2026-07-26): 금지 구간은 완벽히 지켜졌는데(간주에 줄이 하나도 안 들어갔다)
# 앵커 줄 52개 중 4개만 자막 시각 ±5초 안에 들어왔고 중앙값이 +29.6초 밀렸다. DP가 간주를
# 피한 뒤, 여전히 자유로운 배치 중에서 star가 24.9~58.8초를 덮는 쪽을 골랐기 때문이다.
# 아래 테스트는 균일 바닥 emission에서 그 자유도를 그대로 만들어 양성 제약이 그것을 닫는지 본다.


def test_negative_constraint_alone_leaves_the_placement_free():
    """대조군 — 금지 구간만으로는 줄이 자막 시각으로 가지 않는다 (실측 실패의 재현)."""
    engine = _engine()
    results = _align(engine, LINES, _flat_emission(), forbidden_spans=[(0.0, 6.0)])
    # 금지 구간은 지켜졌지만 세 줄이 6초 직후에 몰려 있다 — 「어디여야 하는지」는 말하지 않았다
    starts = [r.start_time for r in results]
    assert all(s >= 6.0 - SEC_PER_FRAME for s in starts)
    assert max(starts) - min(starts) < 0.5, f"제약 없는 자유도가 재현되지 않았다: {starts}"


def test_line_starts_pin_each_anchored_line_to_its_caption_time():
    engine = _engine()
    want = {0: 1.0, 1: 4.0, 2: 8.0}
    results = _align(engine, LINES, _flat_emission(), line_starts=want)

    d = engine.get_last_caption_anchor()
    assert d["adopted"] is True and d["blocks"] == 3
    for i, t in want.items():
        assert abs(results[i].start_time - t) <= 5.0 + SEC_PER_FRAME, (
            f"{i}번 줄이 자막 시각 {t}s의 창을 벗어났다: {results[i].start_time}"
        )
    # 균일 바닥에서는 제약이 음향 근거를 전혀 잃지 않는다 (합성보컬 곡의 상황)
    assert d["loss"] == 0.0


def test_line_starts_and_forbidden_spans_compose():
    engine = _engine()
    results = _align(
        engine,
        LINES,
        _flat_emission(),
        forbidden_spans=[(3.0, 6.0)],
        line_starts={0: 1.0, 1: 7.0, 2: 9.0},
    )
    d = engine.get_last_caption_anchor()
    assert d["adopted"] is True and d["blocks"] == 3 and d["frames"] > 0
    # 금지 구간에는 글자가 없고, 각 줄은 자기 창 안에 있다
    assert all(not (3.0 <= w.start < 6.0) for w in _all_chars(results))
    assert results[1].start_time >= 2.0 and results[2].start_time >= 4.0


def test_line_starts_keep_the_lines_in_order():
    """창이 ±5초로 겹치므로 순차화가 없으면 줄 순서가 뒤집힌다."""
    engine = _engine()
    results = _align(engine, LINES, _flat_emission(), line_starts={0: 1.0, 1: 1.5, 2: 2.0})
    starts = [r.start_time for r in results]
    ends = [r.end_time for r in results]
    assert starts == sorted(starts), f"줄 시작이 역순이다: {starts}"
    assert all(s >= e - SEC_PER_FRAME for s, e in zip(starts[1:], ends[:-1])), (
        f"줄이 겹친다: starts={starts} ends={ends}"
    )


def test_unanchored_lines_float_freely_inside_their_block():
    # 1번 줄은 매칭되지 않았다 — 0번 앵커와 2번 앵커 사이에서 자유롭게 놓인다
    engine = _engine()
    results = _align(engine, LINES, _flat_emission(), line_starts={0: 1.0, 2: 8.0})
    assert engine.get_last_caption_anchor()["blocks"] == 2
    assert results[0].start_time <= results[1].start_time <= results[2].start_time
    assert abs(results[2].start_time - 8.0) <= 5.0 + SEC_PER_FRAME


def test_positive_constraint_is_rejected_when_it_drags_characters_off_their_peaks():
    """앵커가 틀리면 맞는 줄을 끌고 간다 — 그 경우 글자별 지지도가 떨어져 기각된다.

    이것이 정상 곡(봉우리가 있는 posterior)을 지키는 유일한 장치다. 균일 바닥 곡에서는
    이 검사가 무력하다는 것을 위 테스트가 함께 못박고 있다.
    """
    emission, used = _peaky_emission(LINES)
    baseline = _align(_engine(), LINES, emission)
    engine = _engine()
    # 실제 발성은 앞쪽(0 ~ used*0.02s)인데 자막이 곡 끝을 가리킨다고 하면
    results = _align(engine, LINES, emission, line_starts={0: 9.0, 1: 9.2, 2: 9.4})

    d = engine.get_last_caption_anchor()
    assert d["adopted"] is False and d["loss"] > d["max_loss"]
    assert [w.start for w in _all_chars(results)] == [w.start for w in _all_chars(baseline)]
    assert used * SEC_PER_FRAME < 9.0, "픽스처가 의도한 구성이 아니다"


def test_healthy_alignment_survives_a_correct_positive_constraint():
    """자막이 맞는 정상 곡에서는 제약이 있어도 1패스와 같은 배치가 나온다 (창이 넉넉하다)."""
    emission, _ = _peaky_emission(LINES, frames_per_token=4)
    baseline = _align(_engine(), LINES, emission)
    truth = {i: (r.start_time or 0.0) for i, r in enumerate(baseline)}
    engine = _engine()
    results = _align(engine, LINES, emission, line_starts=truth)
    d = engine.get_last_caption_anchor()
    assert d["adopted"] is True and d["loss"] <= d["max_loss"]
    for i, r in enumerate(results):
        assert abs(r.start_time - truth[i]) < 0.5, f"{i}번 줄이 움직였다"


def test_infeasible_block_abandons_the_whole_positive_constraint():
    """창을 늘려도 모자라면 조용히가 아니라 기록을 남기고 전체를 포기한다.

    마지막 앵커가 곡 끝에 붙어 있어 늘릴 여지가 없는 경우다 (창 확장의 상한은 곡 길이다).
    """
    emission = _flat_emission(frames=60)  # 1.2초
    baseline = _align(_engine(), LINES, emission)
    engine = _engine(caption_anchor_window_sec=0.05)
    results = _align(engine, LINES, emission, line_starts={0: 0.1, 1: 0.2, 2: 1.15})
    d = engine.get_last_caption_anchor()
    assert d["skipped"] == "infeasible_block" and "adopted" not in d
    fail_lo, fail_hi, f_lo, f_hi, need = d["block_fail"]
    assert f_hi - f_lo < need
    assert [w.start for w in _all_chars(results)] == [w.start for w in _all_chars(baseline)]


def test_block_windows_widen_before_giving_up():
    """창이 살짝 모자라면 뒤로 늘려 제약을 약화시킨다 — 포기보다 안전한 실패 방향이다."""
    engine = _engine(caption_anchor_window_sec=0.02)
    results = _align(engine, LINES, _flat_emission(), line_starts={0: 1.0, 1: 1.05, 2: 1.1})
    d = engine.get_last_caption_anchor()
    assert "skipped" not in d
    assert d["widened_blocks"] >= 1, f"창을 늘린 기록이 없다: {d}"
    assert len(_all_chars(results)) == sum(len(_ids(t)) for t in LINES)


def test_no_nan_with_the_positive_constraint():
    engine = _engine()
    results = _align(
        engine, LINES, _flat_emission(), forbidden_spans=[(3.0, 6.0)], line_starts={0: 1.0, 2: 8.0}
    )
    for w in _all_chars(results):
        assert math.isfinite(w.start) and math.isfinite(w.end)
        assert w.confidence is not None and math.isfinite(w.confidence) and w.confidence > 0.0
    d = engine.get_last_caption_anchor()
    assert all(math.isfinite(v) for v in d["support"]) and math.isfinite(d["loss"])
    # 창 정렬은 전곡 경로가 아니므로 경로 총점은 남기지 않는다
    assert "path_score" not in d


def test_emission_is_restored_after_masking():
    """마스킹은 제자리에서 하고 반드시 되돌린다 — 뒤따르는 심판·greedy가 원본을 봐야 한다."""
    emission = _flat_emission()
    before = emission.clone()
    _align(_engine(), LINES, emission, forbidden_spans=[(0.4, 6.0)])
    assert torch.equal(emission[:, :, : len(VOCAB)], before)
