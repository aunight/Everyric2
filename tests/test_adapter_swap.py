"""MMS 어댑터 스왑 회귀 테스트.

`ja`/`ko`/`en`은 전부 같은 베이스(`facebook/mms-1b-all`)를 쓰고 어댑터만 다르다.
언어가 바뀔 때 `from_pretrained`를 통째로 다시 하지 않고 `load_adapter`만 부르는
최적화가 들어가 있는데, 여기서 조용히 틀릴 수 있는 지점이 둘이다.

1. 프로세서가 어댑터를 따라가지 않으면 vocab/blank가 이전 언어인 채로 정렬된다.
2. 상주 모델을 제자리에서 바꾸므로, 다른 언어의 forward 중에 끼어들면 어댑터와
   가사 토큰의 언어가 어긋난다.

서버 3090 실측(2026-07-25)으로 확인한 값을 이 테스트가 고정한다:
- vocab 크기 eng=154 / kor=1330 / jpn=2268, pad_token_id는 셋 다 0
- ko 정렬 → 어댑터 스왑 → ja 정렬의 결과가 전체 재로드 경로와 비트 단위로 동일
  (word start/end 최대차 0.000000000s, 글자 불일치 0개)
여기서는 모델 가중치 없이 로드 경로의 상태 전이만 검증한다.
"""

import threading

import pytest

from everyric2.alignment.ctc_engine import (
    MMS_BASE_MODEL,
    MMS_LANG_CODES,
    CTCEngine,
    clear_shared_ctc_engine,
)
from everyric2.config.settings import AlignmentSettings

# 서버 실측 vocab 크기 — 스왑 후 이 값이 따라 바뀌어야 한다.
# eng는 더 이상 선택되지 않지만(test_english_never_loads_the_eng_adapter) 크기를 남겨
# 스텁이 잘못된 어댑터를 로드했을 때 vocab 크기로 잡히게 한다.
MEASURED_VOCAB_SIZES = {"eng": 154, "kor": 1330, "jpn": 2268, "cmn-script_simplified": 4495}


class _StubTokenizer:
    def __init__(self):
        self.target_lang = None
        self.pad_token_id = 0

    def set_target_lang(self, code):
        self.target_lang = code

    def get_vocab(self):
        return {f"t{i}": i for i in range(MEASURED_VOCAB_SIZES.get(self.target_lang, 32))}


class _StubProcessor:
    created = 0

    def __init__(self):
        self.tokenizer = _StubTokenizer()

    @classmethod
    def from_pretrained(cls, name):
        assert name == MMS_BASE_MODEL
        cls.created += 1
        return cls()


class _StubModel:
    """load_adapter 호출을 기록하는 최소 모델."""

    loaded = 0

    def __init__(self):
        self.adapter_calls = []
        self.eval_calls = 0

    @classmethod
    def from_pretrained(cls, name):
        cls.loaded += 1
        return cls()

    def to(self, device):
        self.device = device
        return self

    def load_adapter(self, code):
        self.adapter_calls.append(code)

    def eval(self):
        self.eval_calls += 1
        return self


@pytest.fixture
def stubbed(monkeypatch):
    """가중치 로더만 스텁으로 갈아끼운다 (다운로드/GPU 없이 로드 경로만 검증).

    transformers는 `_LazyModule`이라 모듈 어트리뷰트를 바꿔치기해도
    `from transformers import Wav2Vec2ForCTC`는 진짜 클래스를 돌려준다(실측). 그래서
    클래스가 아니라 진짜 클래스의 `from_pretrained`를 갈아끼운다 — 이걸 안 하면 테스트가
    조용히 1B 가중치를 진짜로 로드한다.
    """
    import transformers

    _StubProcessor.created = 0
    _StubModel.loaded = 0
    monkeypatch.setattr(
        transformers.AutoProcessor, "from_pretrained", _StubProcessor.from_pretrained
    )
    monkeypatch.setattr(
        transformers.Wav2Vec2ForCTC, "from_pretrained", _StubModel.from_pretrained
    )

    import torch

    engine = CTCEngine(AlignmentSettings())
    engine._device = torch.device("cpu")  # 로컬 GPU를 건드리지 않는다
    return engine


# --------------------------------------------------------------------------
# 인스턴스 상태 계약
# --------------------------------------------------------------------------


def test_fresh_engine_declares_all_swap_state():
    # 회귀: 프로세서 캐시 필드가 __init__에 없어 첫 MMS 로드가 AttributeError로 죽었다.
    # 스왑 판단에 쓰이는 필드는 전부 생성자에서 선언돼 있어야 한다.
    engine = CTCEngine(AlignmentSettings())
    assert engine._mms_processor_obj is None
    assert engine._mms_processor_lang is None
    assert engine._current_base is None
    assert engine._current_adapter is None
    assert isinstance(engine._model_lock, type(threading.RLock()))


def test_lang_map_shares_one_base_model():
    # 스왑 최적화의 전제 — ja/ko/en이 같은 베이스여야 어댑터만 바꿔도 된다
    from everyric2.alignment.ctc_engine import LANG_MODEL_MAP

    assert {LANG_MODEL_MAP[lang] for lang in ("ja", "ko", "en")} == {MMS_BASE_MODEL}
    assert LANG_MODEL_MAP["zh"] != MMS_BASE_MODEL  # zh는 다른 베이스 → 전체 재로드
    for lang in ("ko", "ja", "en"):
        assert lang in MMS_LANG_CODES


# --------------------------------------------------------------------------
# 로드 경로: 언제 스왑하고 언제 전체 재로드하는가
# --------------------------------------------------------------------------


def test_first_load_pulls_base_and_adapter(stubbed):
    stubbed._ensure_model_loaded("ko")
    assert _StubModel.loaded == 1
    assert stubbed._current_base == MMS_BASE_MODEL
    assert stubbed._current_adapter == "kor"
    assert stubbed._model.adapter_calls == ["kor"]
    assert stubbed._model.eval_calls >= 1


def test_language_switch_swaps_adapter_without_reloading_base(stubbed):
    stubbed._ensure_model_loaded("ko")
    model = stubbed._model
    stubbed._ensure_model_loaded("ja")

    assert _StubModel.loaded == 1, "베이스가 다시 로드됐다 — 스왑이 안 먹었다"
    assert stubbed._model is model, "모델 인스턴스가 교체됐다"
    assert model.adapter_calls == ["kor", "jpn"]
    assert stubbed._current_adapter == "jpn"


def test_processor_follows_the_adapter(stubbed):
    """가장 위험한 실패 모드: 어댑터만 바뀌고 토크나이저가 그대로 남는 것."""
    stubbed._ensure_model_loaded("ko")
    assert stubbed._processor.tokenizer.target_lang == "kor"
    assert len(stubbed._processor.tokenizer.get_vocab()) == MEASURED_VOCAB_SIZES["kor"]

    stubbed._ensure_model_loaded("ja")
    assert stubbed._processor.tokenizer.target_lang == "jpn"
    assert len(stubbed._processor.tokenizer.get_vocab()) == MEASURED_VOCAB_SIZES["jpn"]

    # 세 번째 어댑터로 cmn을 쓴다 — force_mms면 zh도 MMS 어댑터 경로로 들어온다
    stubbed._ensure_model_loaded("zh", force_mms=True)
    assert stubbed._processor.tokenizer.target_lang == "cmn-script_simplified"
    assert (
        len(stubbed._processor.tokenizer.get_vocab())
        == MEASURED_VOCAB_SIZES["cmn-script_simplified"]
    )

    # blank id는 세 어댑터 모두 0 (실측) — 스왑이 이걸 흔들면 정렬이 통째로 깨진다
    assert stubbed._processor.tokenizer.pad_token_id == 0


def test_english_never_loads_the_eng_adapter(stubbed):
    """영어는 kor 어댑터로 정렬한다 — eng는 vocab 154개에 한글·가나가 0개다.

    순수 영어 4곡 실측에서 eng의 정확도 우위가 없었고(잔차 중앙값 차 ≤ 0.035초 = CTC
    프레임 2칸 이하, 부호는 곡마다 뒤집힘), 반대로 한 글자라도 CJK가 섞이면 eng는 그
    글자를 통째로 놓친다. 상세 수치는 tests/test_adapter_coverage.py 참조.
    """
    assert MMS_LANG_CODES["en"] == "kor"

    stubbed._ensure_model_loaded("en")
    assert stubbed._current_adapter == "kor"
    assert stubbed._model.adapter_calls == ["kor"]
    assert stubbed._processor.tokenizer.target_lang == "kor"
    assert "eng" not in stubbed._model.adapter_calls

    # 매핑에 없는 언어도 eng로 떨어지지 않아야 한다
    stubbed._ensure_model_loaded("xx", force_mms=True)
    assert stubbed._current_adapter == "kor"
    assert "eng" not in stubbed._model.adapter_calls


def test_same_language_reload_is_a_noop(stubbed):
    stubbed._ensure_model_loaded("ko")
    stubbed._ensure_model_loaded("ko")
    assert _StubModel.loaded == 1
    assert stubbed._model.adapter_calls == ["kor"], "같은 언어에 load_adapter 재호출"


def test_processor_is_built_once_across_switches(stubbed):
    """성능 계약: 프로세서 생성(3.65초)이 언어마다 반복되면 스왑 이득이 날아간다."""
    stubbed._ensure_model_loaded("ko")
    stubbed._ensure_model_loaded("ja")
    stubbed._ensure_model_loaded("en")
    stubbed._ensure_model_loaded("ko")  # 되돌리기

    assert _StubProcessor.created == 1, (
        f"프로세서를 {_StubProcessor.created}번 만들었다 — 전환마다 재생성 중"
    )
    assert stubbed._processor.tokenizer.target_lang == "kor"
    assert len(stubbed._processor.tokenizer.get_vocab()) == MEASURED_VOCAB_SIZES["kor"]


def test_retarget_only_fires_on_actual_change(stubbed, monkeypatch):
    stubbed._ensure_model_loaded("ko")
    tok = stubbed._processor.tokenizer
    calls = []
    real_set = tok.set_target_lang
    monkeypatch.setattr(
        tok, "set_target_lang", lambda c: (calls.append(c), real_set(c))[1]
    )

    stubbed._ensure_model_loaded("ko")  # 같은 언어 → 재조준 없음
    assert calls == []
    stubbed._ensure_model_loaded("ja")
    assert calls == ["jpn"]


def test_force_mms_keeps_cache_key_distinct(stubbed):
    stubbed._ensure_model_loaded("ko")
    stubbed._ensure_model_loaded("ko", force_mms=True)
    # 캐시 키가 다르므로 재진입하지만, 베이스가 같으니 재로드는 없어야 한다
    assert _StubModel.loaded == 1
    assert stubbed._current_lang == "ko_mms"
    assert stubbed._current_adapter == "kor"


def test_non_mms_base_forces_full_reload(stubbed, monkeypatch):
    """zh는 다른 베이스 → 스왑 분기로 새면 안 된다 (틀린 모델로 정렬)."""
    import transformers

    monkeypatch.setattr(
        transformers.Wav2Vec2Processor,
        "from_pretrained",
        staticmethod(lambda name: _StubProcessor()),
    )

    stubbed._ensure_model_loaded("ko")
    mms_model = stubbed._model
    stubbed._ensure_model_loaded("zh")

    assert stubbed._model is not mms_model, "zh가 MMS 모델을 그대로 썼다"
    assert stubbed._current_base != MMS_BASE_MODEL
    assert stubbed._current_adapter is None

    # zh에서 다시 ja로 갈 때도 스왑이 아니라 전체 재로드여야 한다
    loaded_before = _StubModel.loaded
    stubbed._ensure_model_loaded("ja")
    assert _StubModel.loaded == loaded_before + 1
    assert stubbed._current_base == MMS_BASE_MODEL
    assert stubbed._current_adapter == "jpn"


# --------------------------------------------------------------------------
# 웜 캐시 싱글턴 계약
# --------------------------------------------------------------------------


def test_shared_engine_is_a_singleton_when_warm(monkeypatch):
    from everyric2.config import settings as settings_mod
    from everyric2.alignment.ctc_engine import get_shared_ctc_engine

    cfg = settings_mod.get_settings()
    monkeypatch.setattr(cfg.server, "warm_models", True)
    clear_shared_ctc_engine()
    try:
        a = get_shared_ctc_engine(AlignmentSettings())
        b = get_shared_ctc_engine(AlignmentSettings())
        assert a is b, "웜 캐시가 매번 새 엔진을 만들면 어댑터 스왑 이득이 사라진다"
        assert a._mms_processor_obj is None
    finally:
        clear_shared_ctc_engine()


def test_clear_shared_engine_drops_processor_cache(monkeypatch):
    from everyric2.config import settings as settings_mod
    from everyric2.alignment.ctc_engine import get_shared_ctc_engine

    cfg = settings_mod.get_settings()
    monkeypatch.setattr(cfg.server, "warm_models", True)
    clear_shared_ctc_engine()
    try:
        a = get_shared_ctc_engine(AlignmentSettings())
        a._mms_processor_obj = object()
        a._mms_processor_lang = "kor"
        clear_shared_ctc_engine()
        b = get_shared_ctc_engine(AlignmentSettings())
        assert b is not a
        assert b._mms_processor_obj is None, "VRAM 가드 후에도 낡은 프로세서가 남았다"
        assert b._mms_processor_lang is None
    finally:
        clear_shared_ctc_engine()


def test_warm_disabled_returns_fresh_engines(monkeypatch):
    from everyric2.config import settings as settings_mod
    from everyric2.alignment.ctc_engine import get_shared_ctc_engine

    cfg = settings_mod.get_settings()
    monkeypatch.setattr(cfg.server, "warm_models", False)
    assert get_shared_ctc_engine(AlignmentSettings()) is not get_shared_ctc_engine(
        AlignmentSettings()
    )


# --------------------------------------------------------------------------
# 동시성: 스왑이 남의 forward 중간에 끼어들지 않는가
# --------------------------------------------------------------------------


def test_align_holds_the_model_lock(stubbed, monkeypatch):
    """align이 모델 락을 잡은 채로 로드+추론을 수행하는지."""
    held = {}

    real_ensure = stubbed._ensure_model_loaded

    def spy(language, force_mms=False):
        # 락을 이미 잡고 있다면 다른 스레드는 acquire에 실패해야 한다
        held["locked_during_load"] = not _acquirable_from_other_thread(stubbed._model_lock)
        return real_ensure(language, force_mms=force_mms)

    monkeypatch.setattr(stubbed, "_ensure_model_loaded", spy)
    monkeypatch.setattr(
        stubbed, "_align_cjk", lambda *a, **k: _mark(held, "locked_during_align", stubbed)
    )

    import numpy as np

    from everyric2.audio.loader import AudioData
    from everyric2.inference.prompt import LyricLine

    audio = AudioData(
        waveform=np.zeros(16000, dtype=np.float32), sample_rate=16000, duration=1.0
    )
    stubbed.align(audio, [LyricLine(text="가", line_number=1)], language="ko")

    assert held["locked_during_load"] is True
    assert held["locked_during_align"] is True


def _mark(held, key, engine):
    held[key] = not _acquirable_from_other_thread(engine._model_lock)
    return []


def _acquirable_from_other_thread(lock) -> bool:
    """다른 스레드에서 논블로킹 acquire가 되는지 (RLock은 소유 스레드만 재진입 가능)."""
    result = {}

    def probe():
        got = lock.acquire(blocking=False)
        result["got"] = got
        if got:
            lock.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join(timeout=5)
    return result.get("got", False)


def test_lock_is_reentrant_for_the_same_thread(stubbed):
    # align 안에서 다시 align을 부르는 중첩 호출이 자기 락에 걸려 죽으면 안 된다
    with stubbed._model_lock:
        assert stubbed._model_lock.acquire(blocking=False)
        stubbed._model_lock.release()
