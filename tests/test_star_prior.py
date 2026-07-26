"""star 프레임별 성형 회귀 테스트 — «가격 차이»가 동점을 실제로 없애는지 못박는다.

검증 전략은 ``test_emission_mask.py``와 같다: GPU도 실오디오도 없으므로 사고를 재현하는
emission을 직접 구성한다. **균일 바닥 emission이 사고의 핵심이다** — star 열이 상수 0이면
「글자를 앞으로 몰고 뒤를 star로 비우기」가 최적해가 된다. 여기에 «6초 전은 무성, 6초부터
유성»이라는 presence를 주면, 글자 하나가 유성 프레임을 차지할 때마다 star가 그 프레임을
흡수할 비용(weight)이 절약되므로 **모든 글자가 유성 구간으로 가는 배치가 프레임당 weight
nats씩 이긴다** — 유성 구간 «안»의 동점은 남지만(균일 바닥의 본성) 무성/유성 사이의 동점이
사라진다. 그것이 간주 오배치의 교정이다.

못박는 것: 성형이 글자를 유성 구간으로 밀어내는가, presence가 없으면(또는 weight 0이면)
기존 동작과 완전히 동일한가, 금지 구간 마스킹과 함께 걸려도 서로를 깨지 않는가,
presence 신호 정제(이동평균·보간)가 무성 자음 틈에서 star를 공짜로 만들지 않는가.
"""

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from everyric2.alignment.ctc_engine import CTCEngine, _resolve_token_char
from everyric2.alignment.star_prior import (
    frame_rms,
    star_frame_scores,
    vocal_presence_from_f0,
    vocal_presence_from_stems,
)
from everyric2.config.settings import AlignmentSettings
from everyric2.inference.prompt import LyricLine

VOCAB = json.loads(
    (Path(__file__).parent / "fixtures" / "mms_kor_vocab.json").read_text(encoding="utf-8")
)
BLANK_ID = 0

SAMPLES_PER_FRAME = 320  # 20ms 프레임 규약 @16kHz
FRAMES = 500  # = 10.0초
SEC_PER_FRAME = 0.02

LINES = ["아침이 오면", "너를 만나러", "달려갈 거야"]

# 6초 전은 무성(간주), 6초부터 유성(가창) — 10ms 격자, 워커가 주는 것과 같은 모양
_TIMES_10MS = np.arange(1000) * 0.01 + 0.005
VOICED_FROM_6S = (_TIMES_10MS >= 6.0).astype(np.float64)


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


def _engine(**kwargs):
    return CTCEngine(AlignmentSettings(star_tokens=True, align_chunk_sec=0.0, **kwargs))


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
# 1) 기전: presence가 글자를 유성 구간으로 밀어낸다 (사고 재현 → 교정)
# --------------------------------------------------------------------------


def test_without_presence_lyrics_cram_forward():
    """대조군 — star가 상수 0이면 사고가 그대로 재현된다 (test_emission_mask와 동일 서명)."""
    results = _align(_engine(), LINES, _flat_emission())
    chars = _all_chars(results)
    assert chars and chars[-1].end < 1.0


def test_presence_moves_every_character_into_the_voiced_region():
    engine = _engine()
    results = _align(
        engine, LINES, _flat_emission(), vocal_presence=(_TIMES_10MS, VOICED_FROM_6S)
    )
    chars = _all_chars(results)
    assert len(chars) == sum(len(_ids(t)) for t in LINES)
    # 프레임 경계 반올림 여유 1프레임 — 6초 «전»의 무성 구간에는 글자가 없어야 한다
    assert all(w.start >= 6.0 - SEC_PER_FRAME for w in chars), (
        f"무성 구간에 글자가 남았다: {[(w.word, round(w.start, 2)) for w in chars][:5]}"
    )
    assert all(w.end <= 10.0 + SEC_PER_FRAME for w in chars)
    # star가 간주(0~6초)를 흡수했다는 기록이 남아야 한다
    assert engine._last_star_spans and engine._last_star_spans[0][0] < 0.1
    assert engine._last_star_spans[0][1] >= 5.9
    # 성형 진단 기록: 유성이 곡의 40%이므로 절반 넘게 가격이 매겨진 프레임이 ~40%다
    assert engine._last_star_prior is not None
    assert 0.3 <= engine._last_star_prior["priced_frac"] <= 0.5


def test_weight_zero_disables_shaping_even_with_presence():
    results = _align(
        _engine(star_prior_weight=0.0),
        LINES,
        _flat_emission(),
        vocal_presence=(_TIMES_10MS, VOICED_FROM_6S),
    )
    chars = _all_chars(results)
    assert chars and chars[-1].end < 1.0  # 대조군과 같은 사고 서명


def test_presence_and_forbidden_spans_compose():
    """금지 구간(하드)과 성형(소프트)이 함께 걸려도 서로를 깨지 않는다.

    금지 마스킹은 star 열을 살려 두므로(경로가 그 구간을 star로 통과해야 한다) 성형된
    가격도 그대로 남는다 — 무성 구간의 star는 여전히 0(싸다)이고 정렬은 채택된다.
    """
    engine = _engine()
    results = _align(
        engine,
        LINES,
        _flat_emission(),
        forbidden_spans=[(0.0, 6.0)],
        vocal_presence=(_TIMES_10MS, VOICED_FROM_6S),
    )
    chars = _all_chars(results)
    assert all(w.start >= 6.0 - SEC_PER_FRAME for w in chars)
    decision = engine.get_last_caption_anchor()
    assert decision["adopted"] is True


# --------------------------------------------------------------------------
# 2) 신호 정제: 이동평균·보간
# --------------------------------------------------------------------------


def test_presence_bridges_unvoiced_consonant_gaps():
    """노래 도중의 f0=0 틈(무성 자음·호흡 수십 ms)이 star를 공짜로 만들면 안 된다."""
    times = np.arange(600) * 0.01
    f0 = np.full(600, 220.0)
    f0[300:304] = 0.0  # 40ms 무성 틈 (s/t/k류)
    out = vocal_presence_from_f0(f0, times, smooth_sec=0.4)
    assert out is not None
    _, presence = out
    assert presence[302] > 0.8  # 틈 한복판에서도 presence가 거의 유지된다


def test_presence_ramps_at_interlude_edges():
    times = np.arange(1200) * 0.01
    f0 = np.where(times >= 6.0, 220.0, 0.0)
    out = vocal_presence_from_f0(f0, times, smooth_sec=0.4)
    assert out is not None
    _, presence = out
    i = np.searchsorted(times, 6.0)
    assert presence[i - 40] < 0.1  # 경계 0.4초 전 — 아직 싸다
    assert 0.3 <= presence[i] <= 0.7  # 경계 위 — 선형 완충의 한가운데
    assert presence[i + 40] > 0.9  # 경계 0.4초 뒤 — 완전히 비싸다


def test_presence_rejects_unusable_signals():
    assert vocal_presence_from_f0(np.array([]), np.array([]), 0.4) is None
    assert vocal_presence_from_f0(np.array([100.0]), np.array([0.0]), 0.4) is None
    assert vocal_presence_from_f0(np.zeros(5), np.zeros(3), 0.4) is None


def test_stem_dominance_separates_interlude_from_singing():
    """우세도 경로 — f0가 못 가른 간주를 스템 비율이 가른다 (실측 근거는 모듈 주석).

    0~6초는 반주만 크고(간주) 6초부터 보컬이 우세한 합성 스템을 만든다. 보컬 스템에
    간주 «블리드»(작은 잔류 신호)를 일부러 남겨 둔다 — f0 유성 지시자를 죽였던 바로
    그 조건에서 우세도는 살아 있어야 한다.
    """
    sr = 16000
    t = np.arange(12 * sr) / sr
    bleed = 0.05 * np.sin(2 * np.pi * 220 * t)  # 간주에도 남는 보컬 스템 잔류
    voice = np.where(t >= 6.0, 0.5, 0.0) * np.sin(2 * np.pi * 220 * t)
    vocals = voice + bleed
    accomp = np.where(t < 6.0, 0.6, 0.2) * np.sin(2 * np.pi * 110 * t)
    out = vocal_presence_from_stems(vocals, accomp, sr, smooth_sec=0.4)
    assert out is not None
    times, presence = out
    early = presence[(times > 1.0) & (times < 5.0)]
    late = presence[(times > 7.0) & (times < 11.0)]
    assert float(early.mean()) < 0.1, "간주(반주 우세)에서 presence가 낮아야 star가 싸다"
    assert float(late.mean()) > 0.9, "가창(보컬 우세)에서 presence가 높아야 글자가 이긴다"


def test_stem_presence_rejects_degenerate_input():
    assert vocal_presence_from_stems(np.zeros(10), np.zeros(3), 16000) is None
    assert vocal_presence_from_stems(np.zeros(0), np.zeros(0), 16000) is None


def test_frame_rms_grid_convention():
    sr = 16000
    w = np.ones(sr)  # 1초 상수 신호 → RMS 1.0 × 100프레임
    r = frame_rms(w, sr, hop_sec=0.01)
    assert r.shape == (100,)
    assert r[50] == pytest.approx(1.0)
    # 스테레오는 모노 평균 후 계산된다
    r2 = frame_rms(np.stack([w, -w]), sr, hop_sec=0.01)
    assert r2[50] == pytest.approx(0.0)


def test_star_frame_scores_grid_and_clamp():
    times = np.array([0.005, 0.015, 0.025, 0.035])
    presence = np.array([0.0, 1.0, 1.0, 0.0])
    vals = star_frame_scores(times, presence, num_frames=2, sec_per_frame=0.02, weight=2.0)
    assert vals.shape == (2,)
    # 프레임 중심 0.01s — presence 0.5 보간 → -2.0 × 0.5
    assert vals[0] == pytest.approx(-1.0)
    assert -2.0 <= vals[1] <= 0.0
    # 신호 범위 밖은 0 — 신호가 없는 곳에서 star를 비싸게 만들면 안 된다
    far = star_frame_scores(times, presence, num_frames=10, sec_per_frame=1.0, weight=2.0)
    assert far[5] == 0.0
    assert np.all(star_frame_scores(times, presence, 4, 0.02, weight=0.0) == 0.0)
