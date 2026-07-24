"""대문자 라틴 글자 정렬 회귀 테스트.

no-mock 데이터: vocab은 실제 MMS 한국어 어댑터 vocab을 그대로 기록한 fixture
(`fixtures/mms_kor_vocab.json`, 1330개). 서버 3090 실측으로 eng/kor/jpn 세 어댑터
모두 ASCII 대문자가 0개, 소문자가 26개임을 확인했고 fixture도 같다.

배경(실측 피해): 대문자를 vocab에 그대로 조회하면 미스가 나 그 글자가 char_info에
들어가지 않았다 — "DA DA RA DA DA" 같은 전부 대문자인 줄은 word_segments가 0개가 되고,
"심장이 Up down" 같은 한영 혼용 줄은 라인 시각이 잡히지 않아 0.00초에 박혔다.
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

# 실측된 MMS 어댑터 blank id (eng/kor/jpn 모두 pad_token_id=0)
BLANK_ID = 0


# --------------------------------------------------------------------------
# 토큰 선택 규칙 (순수 함수)
# --------------------------------------------------------------------------


def test_fixture_vocab_has_no_ascii_uppercase():
    # 이 회귀의 전제 — 대문자가 vocab에 없다는 사실 자체를 고정한다.
    upper = [c for c in VOCAB if len(c) == 1 and c.isascii() and c.isupper()]
    lower = [c for c in VOCAB if len(c) == 1 and c.isascii() and c.islower()]
    assert upper == []
    assert len(lower) == 26


def test_uppercase_resolves_to_lowercase_token():
    # 대문자는 소문자 토큰으로 조회되어야 한다 (예전엔 None → 정렬에서 탈락)
    for ch in "DARUP":
        assert ch not in VOCAB
        assert _resolve_token_char(ch, VOCAB) == ch.lower()


def test_lowercase_unchanged():
    # 소문자 줄의 기존 동작은 그대로 — 자기 자신을 그대로 쓴다
    for ch in "daruponm":
        assert _resolve_token_char(ch, VOCAB) == ch


def test_hangul_path_unchanged():
    # 한글은 lower()가 항등 → 소문자화 분기를 그냥 통과하고 기존 규칙만 적용된다
    assert _resolve_token_char("안", VOCAB) == "안"  # in-vocab은 원형 유지
    assert _resolve_token_char("뿅", VOCAB) == "뾰"  # OOV 치환은 기존과 동일
    assert _resolve_token_char("얍", VOCAB) == "야"


def test_symbols_still_excluded():
    # vocab 밖 기호는 여전히 None → 정렬에서 제외 (소문자화가 새 토큰을 만들지 않는다)
    for ch in ("(", ")", "!", "?"):
        if ch not in VOCAB:
            assert _resolve_token_char(ch, VOCAB) is None


def test_in_vocab_char_never_lowercased():
    # vocab에 있는 글자는 1)에서 곧장 반환 — 소문자화가 끼어들면 안 된다
    for ch in list(VOCAB)[:50]:
        if len(ch) == 1:
            assert _resolve_token_char(ch, VOCAB) == ch


# --------------------------------------------------------------------------
# _align_cjk 통합 — 원문 대소문자 보존과 실제 정렬 산출
# --------------------------------------------------------------------------


class _FakeTokenizer:
    """실제 vocab을 그대로 들고 있는 토크나이저 (get_vocab/pad_token_id만 쓰인다)."""

    def __init__(self, vocab):
        self._vocab = vocab
        self.pad_token_id = BLANK_ID

    def get_vocab(self):
        return self._vocab


class _FakeProcessor:
    def __init__(self, vocab):
        self.tokenizer = _FakeTokenizer(vocab)


def _emission_favoring(token_ids: list[int], vocab_size: int, frames_per_token: int = 4):
    """지정한 토큰 열을 순서대로 강하게 지지하는 log_softmax emission을 만든다.

    각 토큰에 연속 프레임 대역을 배정해, forced_align이 그 순서를 그대로 복원하도록 한다.
    실제 모델 emission과 같은 규약(정규화된 log 확률, shape (1, T, V))을 지킨다.
    """
    n_tok = len(token_ids)
    total = n_tok * frames_per_token
    logits = torch.full((1, total, vocab_size), -8.0)
    for i, tid in enumerate(token_ids):
        lo = i * frames_per_token
        logits[0, lo : lo + frames_per_token, tid] = 8.0
    return torch.log_softmax(logits, dim=-1)


def _run_align(engine, lyrics, token_ids):
    """_ctc_log_emission만 합성 emission으로 대체하고 _align_cjk를 실제로 돌린다."""
    emission = _emission_favoring(token_ids, len(VOCAB))
    engine._processor = _FakeProcessor(VOCAB)
    engine._ctc_log_emission = lambda waveform, device: emission  # noqa: ARG005
    engine._device = torch.device("cpu")
    n_samples = emission.shape[1] * 320  # 20ms 프레임 ≈ 16kHz 기준 320 샘플
    waveform = torch.zeros(n_samples)
    return engine._align_cjk(waveform, lyrics, "ko")


def _engine():
    return CTCEngine(AlignmentSettings(star_tokens=False, align_chunk_sec=0.0))


def test_all_uppercase_line_produces_word_segments():
    # 회귀: "DA DA RA DA DA"는 word_segments가 0개였다
    line = LyricLine(text="DA DA RA DA DA", line_number=1)
    # 공백은 vocab 밖이라 탈락, 남는 글자 10개 + 라인 끝 "|" 1개
    letters = [c for c in line.text if _resolve_token_char(c, VOCAB) is not None]
    token_ids = [VOCAB[_resolve_token_char(c, VOCAB)] for c in letters] + [VOCAB["|"]]

    engine = _engine()
    results = _run_align(engine, [line], token_ids)

    words = engine._last_word_timestamps
    assert len(words) == 10, f"대문자 줄에서 글자 타임스탬프가 {len(words)}개"
    assert len(results) == 1
    assert results[0].end_time > results[0].start_time > -1e-9
    # 0.00초에 뭉치지 않고 실제로 시간축에 퍼져야 한다
    assert results[0].end_time > 0.05


def test_uppercase_words_keep_original_case():
    # char_info["char"]가 원본이어야 표시·역매핑 인덱스가 원문 기준으로 유지된다
    line = LyricLine(text="DA DA RA DA DA", line_number=1)
    letters = [c for c in line.text if _resolve_token_char(c, VOCAB) is not None]
    token_ids = [VOCAB[_resolve_token_char(c, VOCAB)] for c in letters] + [VOCAB["|"]]

    engine = _engine()
    _run_align(engine, [line], token_ids)

    got = "".join(w.word for w in engine._last_word_timestamps)
    assert got == "DADARADADA", got
    assert got == got.upper()


def test_mixed_hangul_latin_line_aligns_every_char():
    # 회귀: "심장이 Up down"류 혼용 줄은 전 글자가 0.00초에 박혔다
    line = LyricLine(text="심장이 Up down", line_number=1)
    letters = [c for c in line.text if _resolve_token_char(c, VOCAB) is not None]
    token_ids = [VOCAB[_resolve_token_char(c, VOCAB)] for c in letters] + [VOCAB["|"]]

    engine = _engine()
    results = _run_align(engine, [line], token_ids)

    words = engine._last_word_timestamps
    assert len(words) == len(letters) == 9  # 심장이 + Up + down, 공백 2개 제외
    assert "".join(w.word for w in words) == "심장이Updown"
    # 서로 다른 시각이어야 한다 — 전부 0.00에 뭉치면 회귀
    starts = [round(w.start, 4) for w in words]
    assert len(set(starts)) == len(starts), f"글자 시각이 겹침: {starts}"
    assert results[0].start_time < results[0].end_time


def test_lowercase_line_timings_match_uppercase_equivalent():
    # 같은 줄을 소문자로 쓰나 대문자로 쓰나 정렬 타이밍은 동일해야 한다
    # (조회 토큰이 같으므로) — 다른 것은 반환 글자의 대소문자뿐이다.
    upper = LyricLine(text="DA RA", line_number=1)
    lower = LyricLine(text="da ra", line_number=1)
    letters = [c for c in lower.text if _resolve_token_char(c, VOCAB) is not None]
    token_ids = [VOCAB[c] for c in letters] + [VOCAB["|"]]

    eu = _engine()
    ru = _run_align(eu, [upper], token_ids)
    el = _engine()
    rl = _run_align(el, [lower], token_ids)

    wu, wl = eu._last_word_timestamps, el._last_word_timestamps
    assert len(wu) == len(wl) == 4
    for a, b in zip(wu, wl):
        assert a.word.lower() == b.word
        assert math.isclose(a.start, b.start, abs_tol=1e-12)
        assert math.isclose(a.end, b.end, abs_tol=1e-12)
    assert math.isclose(ru[0].start_time, rl[0].start_time, abs_tol=1e-12)
    assert math.isclose(ru[0].end_time, rl[0].end_time, abs_tol=1e-12)
