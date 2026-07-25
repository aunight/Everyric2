"""CTC-based forced alignment engine using wav2vec2 models.

For Japanese/Korean/Chinese: Uses HuggingFace wav2vec2 models with native character support.
For other languages: Uses torchaudio MMS_FA with Latin alphabet.
"""

import logging
import math
import threading
from collections.abc import Callable
from typing import Literal

import torch
import torchaudio
import torchaudio.functional as F

from everyric2.alignment.base import (
    AlignmentError,
    BaseAlignmentEngine,
    EngineNotAvailableError,
    TranscriptionResult,
    WordTimestamp,
)
from everyric2.alignment.matcher import MatchStats
from everyric2.audio.loader import AudioData
from everyric2.config.settings import AlignmentSettings
from everyric2.inference.prompt import LyricLine, SyncResult

logger = logging.getLogger(__name__)

MMS_BASE_MODEL = "facebook/mms-1b-all"

LANG_MODEL_MAP = {
    "ja": MMS_BASE_MODEL,
    "ko": MMS_BASE_MODEL,
    "zh": "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn",
    "en": MMS_BASE_MODEL,
}

# torchaudio MMS_FA 폴백 경로의 베이스 식별자 (HF 모델 id와 겹치지 않는 이름)
_TORCHAUDIO_MMS_FA = "torchaudio:MMS_FA"

MMS_LANG_CODES = {
    "ko": "kor",
    "ja": "jpn",
    "zh": "cmn-script_simplified",
    # 영어는 'eng'가 아니라 'kor' 어댑터로 정렬한다. eng vocab은 154개뿐이고 한글·가나가
    # 0개라 한 글자라도 CJK가 섞이면 그 글자가 통째로 정렬에서 빠지는데, kor/jpn/cmn은
    # 전부 ASCII 소문자 26개를 갖고 있어 라틴을 똑같이 덮는다. 순수 영어 4곡을 같은
    # 오디오·같은 가사로 세 어댑터에 돌려 유튜브 수동 자막 기준 잔차를 비교한 결과
    # eng의 우위가 없었다 (중앙값 차 ≤ 0.035초 = CTC 프레임 20ms의 2칸 이하이고,
    # 부호도 곡마다 뒤집힌다). 상세 수치는 tests/test_adapter_coverage.py 참조.
    "en": "kor",
}

# 어댑터 vocab이 실제로 덮는 문자 스크립트. facebook/mms-1b-all의 tokenizer vocab.json을
# 직접 센 값이다(2026-07-25, 단일 글자 토큰만):
#     eng 154 — latin_lower 26, latin_ext 51, han 8,    hangul    0, kana  0
#     kor 1330 — latin_lower 26, latin_ext  5, han 2,    hangul 1261, kana  0
#     jpn 2268 — latin_lower 26, latin_ext  1, han 2048, hangul    0, kana 158
#     cmn 4495 — latin_lower 26, latin_ext  5, han 4419, hangul    0, kana  4
# 한 자리 수의 흔적(eng의 han 8개, cmn의 kana 4개)은 그 스크립트를 덮는다고 보지 않는다 —
# 수천 자 규모와 한 자리 수를 같은 'True'로 접으면 커버리지 판정이 뒤집힌다.
#
# 왜 하드코딩인가: 어느 어댑터를 로드할지 **로드 전에** 정해야 하므로 vocab을 조회할 수
# 없다(vocab.json은 HF 캐시가 이미 있을 때만 읽히고, 첫 실행에는 없다). 대신 글자 단위가
# 아니라 스크립트 단위로만 판정한다 — kor vocab의 한글은 1261자로 완성형 11172자를 다
# 담지 못하지만, 빠진 음절은 `_oov_substitute`가 발음이 가까운 in-vocab 음절로 치환하므로
# "kor은 한글을 덮는다"가 실효적으로 성립한다. 실측 대조: 한글 234자 + 라틴 406자인 곡의
# 글자 단위 커버리지는 kor 0.994 / eng 0.632 / jpn 0.628이었고, 아래 스크립트 표가 주는
# 예측(1.000 / 0.634 / 0.634)과 순위가 일치한다.
# 이 표가 실제 vocab과 어긋나지 않는지는 tests/test_adapter_coverage.py가 고정한다.
_ADAPTER_SCRIPTS: dict[str, frozenset[str]] = {
    "kor": frozenset({"latin", "hangul"}),
    "jpn": frozenset({"latin", "kana", "han"}),
    "cmn-script_simplified": frozenset({"latin", "han"}),
}

# 여러 스크립트가 섞였을 때 후보로 두는 언어 → 그 언어를 지목하는 스크립트.
# 삽입 순서가 곧 완전 동점 시의 우선순위다 (_pick_by_coverage의 max가 첫 최대를 남긴다) —
# 기존 단일 스크립트 분기의 우선순위(ja → ko → zh)와 같게 두어 판정이 예측 가능하게 한다.
# 'en'이 없는 것이 의도다: en은 kor 어댑터를 쓰므로 ko 후보와 커버리지가 항상 같고,
# 스크립트가 둘 이상이면 라틴만 있는 후보는 어차피 이길 수 없다.
_MULTILINGUAL_CANDIDATES: dict[str, str] = {
    "ja": "kana",
    "ko": "hangul",
    "zh": "han",
}


def _char_script(char: str) -> str | None:
    """정렬 토큰이 될 수 있는 글자를 스크립트로 분류한다. 그 밖이면 None.

    대문자 라틴도 'latin'이다 — `_resolve_token_char`가 소문자로 조회하므로 어댑터
    vocab에 대문자가 0개여도 실제로 정렬된다. 여기서 커버 불가로 세면 커버리지 판정이
    소문자화 폴백과 어긋난다.
    숫자·구두점·공백은 어느 어댑터로도 노래로 정렬되지 않으므로 분모에서 아예 뺀다.
    """
    code = ord(char)
    if 0x3040 <= code <= 0x30FF:  # Hiragana + Katakana
        return "kana"
    if 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF:  # Hangul
        return "hangul"
    if 0x4E00 <= code <= 0x9FFF:  # CJK Ideographs
        return "han"
    if 0x41 <= code <= 0x5A or 0x61 <= code <= 0x7A:  # A-Z a-z
        return "latin"
    return None


def script_census(text: str) -> dict[str, int]:
    """가사 텍스트의 스크립트별 글자 수 (정렬 대상이 아닌 글자는 세지 않는다)."""
    counts = {"kana": 0, "hangul": 0, "han": 0, "latin": 0}
    for char in text:
        script = _char_script(char)
        if script is not None:
            counts[script] += 1
    return counts


def adapter_coverage(counts: dict[str, int], adapter: str) -> float:
    """이 어댑터 vocab이 덮는 글자 비율 (0~1). 정렬 대상 글자가 없으면 0.0."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    scripts = _ADAPTER_SCRIPTS[adapter]
    return sum(n for script, n in counts.items() if script in scripts) / total


def _pick_by_coverage(counts: dict[str, int]) -> str:
    """스크립트가 섞인 가사에서 쓸 언어를 어댑터 vocab 커버리지로 고른다.

    "어느 문자가 더 많나"로 고르면 안 된다 — 한글 234자 + 라틴 406자인 KPOP 곡이 라틴
    다수라는 이유로 en으로 판정됐고, eng vocab에는 한글이 0개라 순수 한글 15줄이 정렬에서
    통째로 빠져 균등 보간만 됐다(구간 오차 −2.5초 → −11.4초로 단조 악화, 실측). 많은 쪽이
    아니라 **덮는 쪽**을 골라야 한다: kor은 한글과 라틴을 모두 덮으므로 이 곡을 100% 덮는다.

    동점 처리:
      ① 커버리지가 같으면 그 언어를 지목하는 스크립트가 실제로 더 많은 쪽. 한글 100자 +
         한자 100자처럼 ko/ja/zh가 모두 0.5로 묶이는 경우, 가나가 0자인 ja를 이 기준이
         떨어뜨린다.
      ② 그래도 같으면 _MULTILINGUAL_CANDIDATES의 순서(ja → ko → zh). 남은 후보가
         정말로 구별 불가능한 경우이므로 결정론만 확보하면 된다.
    """
    return max(
        _MULTILINGUAL_CANDIDATES,
        key=lambda lang: (
            adapter_coverage(counts, MMS_LANG_CODES[lang]),
            counts[_MULTILINGUAL_CANDIDATES[lang]],
        ),
    )


def detect_language_from_text(text: str) -> tuple[str, bool]:
    """Detect language from lyrics text.

    Returns:
        Tuple of (primary_language, is_multilingual)
        - primary_language: 'ja', 'ko', 'zh', or 'en' (dominant language)
        - is_multilingual: True if multiple scripts detected → recommends MMS 1B-all
    """
    counts = script_census(text)
    ja_count, ko_count, zh_count, en_count = (
        counts["kana"],
        counts["hangul"],
        counts["han"],
        counts["latin"],
    )

    detected = []
    if ja_count > 0:
        detected.append("ja")
    if ko_count > 0:
        detected.append("ko")
    if zh_count > 0 and ja_count == 0:  # CJK without kana = Chinese
        detected.append("zh")
    if en_count > 10:
        detected.append("en")

    is_multilingual = len(detected) >= 2

    if is_multilingual:
        primary = _pick_by_coverage(counts)
        coverage = ", ".join(
            f"{lang}/{MMS_LANG_CODES[lang]}={adapter_coverage(counts, MMS_LANG_CODES[lang]):.3f}"
            for lang in _MULTILINGUAL_CANDIDATES
        )
        logger.info(
            f"Multiple languages detected: {detected} → primary: {primary} "
            f"(chars {counts}, vocab coverage {coverage}), using MMS 1B-all"
        )
        return (primary, True)

    if ja_count > 0:
        return ("ja", False)
    if ko_count > 0:
        return ("ko", False)
    if zh_count > 0:
        return ("zh", False)
    return ("en", False)


_HANGUL_CHO = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")
_HANGUL_JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")
_TENSE_TO_PLAIN = {"ㄲ": "ㄱ", "ㄸ": "ㄷ", "ㅃ": "ㅂ", "ㅆ": "ㅅ", "ㅉ": "ㅈ"}
_GLIDE_TO_PLAIN = {"ㅑ": "ㅏ", "ㅒ": "ㅐ", "ㅕ": "ㅓ", "ㅖ": "ㅔ", "ㅛ": "ㅗ", "ㅠ": "ㅜ"}


def _oov_substitute(char: str, vocab) -> str | None:
    """vocab에 없는 한글 음절을 발음이 가까운 vocab 음절로 치환(정렬 전용).

    된소리→예사소리(ㅃ→ㅂ), 활음 제거(ㅛ→ㅗ), 종성 제거를 점진 적용해 vocab에 있는
    첫 후보를 반환. 한글 음절이 아니거나(괄호·기호) 후보가 없으면 None → 정렬에서 제외.
    예) 뿅(ㅃㅛㅇ)→뽕→봉, 얍(ㅇㅑㅂ)→얌→압. 출력 글자는 원본을 유지하고 타이밍만 차용한다.
    """
    code = ord(char)
    if not (0xAC00 <= code <= 0xD7A3):  # 한글 완성형 음절이 아님
        return None
    s = code - 0xAC00
    cho_i, jung_i, jong_i = s // 588, (s % 588) // 28, s % 28
    cho, jung = _HANGUL_CHO[cho_i], _HANGUL_JUNG[jung_i]
    cho_alts = [cho, _TENSE_TO_PLAIN.get(cho, cho)]
    jung_alts = [jung, _GLIDE_TO_PLAIN.get(jung, jung)]
    seen: set[str] = set()
    for c in cho_alts:
        for j in jung_alts:
            for jo in (jong_i, 0):  # 원래 종성 유지 → 종성 제거 순
                cand = chr(0xAC00 + _HANGUL_CHO.index(c) * 588 + _HANGUL_JUNG.index(j) * 28 + jo)
                if cand == char or cand in seen:
                    continue
                seen.add(cand)
                if cand in vocab:
                    return cand
    return None


def _resolve_token_char(char: str, vocab) -> str | None:
    """정렬 토큰으로 조회할 글자를 고른다. 원문 글자는 호출부가 따로 보존한다.

    1) vocab에 그대로 있으면 그것 — 대문자가 vocab에 있는 모델이면 원형이 우선한다.
    2) 없으면 소문자로 한 번 더 조회. MMS 어댑터 vocab은 라틴 문자가 전부 소문자라
       (eng/kor/jpn 실측: ASCII 대문자 0개, 소문자 26개) 대문자를 그대로 조회하면 미스가
       나 그 글자가 정렬에서 통째로 빠졌다 — "DA DA RA DA DA" 같은 줄은 word_segments가
       0개였고 한영 혼용 줄은 전 글자가 0.00초에 박혔다.
    3) 그래도 없으면 한글 OOV 음절 치환(_oov_substitute).
    한글은 lower()가 항등이라 2)를 그냥 통과하므로 기존 한글 동작은 바뀌지 않는다.
    """
    if char in vocab:
        return char
    lowered = char.lower()
    if lowered != char and lowered in vocab:
        return lowered
    return _oov_substitute(char, vocab)


class CTCEngine(BaseAlignmentEngine):
    def __init__(self, config: AlignmentSettings | None = None):
        super().__init__(config)
        self._model = None
        self._processor = None
        self._current_lang = None
        # 현재 상주 중인 베이스 모델 식별자와 MMS 어댑터 코드 — 언어 전환 시 전체 재로드가
        # 필요한지(베이스가 다름) 어댑터 교체로 충분한지(베이스가 같음) 판단하는 근거다.
        self._current_base: str | None = None
        self._current_adapter: str | None = None
        # 상주 MMS 프로세서와 그것이 현재 겨누고 있는 어댑터 코드. 인스턴스 필드라
        # 엔진을 버리면(clear_shared_ctc_engine) 같이 사라진다.
        self._mms_processor_obj = None
        self._mms_processor_lang: str | None = None
        # 모델 가중치는 인스턴스 하나를 공유하므로, 어댑터 교체가 다른 스레드의 forward
        # 중간에 끼어들면 lm_head/어댑터가 뒤바뀐 채 정렬된다. align 전체를 이 락으로 감싼다.
        self._model_lock = threading.RLock()
        self._device = None
        self._last_word_timestamps: list[WordTimestamp] = []
        self._last_match_stats = None
        # 직전 정렬에서 star 토큰이 흡수한 (start, end) 구간들 — 디버그/진단용
        self._last_star_spans: list[tuple[float, float]] = []

    def is_available(self) -> bool:
        try:
            import torchaudio  # noqa: F401
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor  # noqa: F401

            return True
        except ImportError:
            return False

    def _get_device(self) -> torch.device:
        if self._device is None:
            self._device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return self._device

    def _mms_processor(self, adapter_code: str):
        """상주 MMS 프로세서를 요청한 어댑터 코드로 겨눠서 돌려준다.

        언어마다 프로세서를 새로 만들면 최적화가 첫 전환에서 통째로 날아간다 — 3090 실측:
        AutoProcessor.from_pretrained 3.65초 vs load_adapter 0.23초 vs set_target_lang
        0.000초. 그래서 인스턴스 하나를 두고 다시 겨누기만 한다.

        set_target_lang 반복이 이전 언어의 토큰을 남기지 않는지는 실측으로 확인했다:
        eng/kor/jpn/cmn 4개 코드의 24개 전환 순서 전부 + 같은 코드 반복 설정까지 101회를
        매번 새로 만든 인스턴스와 대조해 vocab 해시·pad_token_id·added_tokens가 전부
        일치했다(불일치 0회). 정렬 결과 자체의 동등성도 별도로 확인했다 — tests/
        test_adapter_swap.py 참조.
        """
        if self._mms_processor_obj is None:
            from transformers import AutoProcessor

            self._mms_processor_obj = AutoProcessor.from_pretrained(MMS_BASE_MODEL)
            self._mms_processor_lang = None
        if self._mms_processor_lang != adapter_code:
            self._mms_processor_obj.tokenizer.set_target_lang(adapter_code)
            self._mms_processor_lang = adapter_code
        return self._mms_processor_obj

    def _ensure_model_loaded(self, language: str, force_mms: bool = False) -> None:
        cache_key = f"{language}_mms" if force_mms else language
        if self._model is not None and self._current_lang == cache_key:
            return

        if not self.is_available():
            raise EngineNotAvailableError(
                "Required packages not installed. Install with: pip install transformers torchaudio"
            )

        device = self._get_device()

        use_mms = force_mms or (
            language in LANG_MODEL_MAP and LANG_MODEL_MAP[language] == MMS_BASE_MODEL
        )

        if use_mms:
            # 매핑에 없는 언어까지 오면 라틴+한글을 덮는 kor로 떨어진다. 예전 기본값
            # 'eng'는 vocab 154개에 한글·가나가 0개라, 정체를 모르는 언어에 대해 가장
            # 잃을 게 많은 선택이었다.
            mms_lang_code = MMS_LANG_CODES.get(language, "kor")

            # 베이스가 이미 MMS면 인코더 가중치는 언어와 무관하게 동일하다 — 언어별로 다른
            # 것은 어댑터 레이어와 lm_head뿐이고 load_adapter가 그 둘을 통째로 덮어쓴다
            # (transformers modeling_wav2vec2.py: _get_adapters + missing_keys 검사로
            # 어댑터 파라미터 전부가 교체됨이 보장된다). 전체 재로드(약 5초) 대신 어댑터만
            # 교체하면 0.2초대이고 결과 emission은 동일하다.
            if self._model is not None and self._current_base == MMS_BASE_MODEL:
                self._processor = self._mms_processor(mms_lang_code)
                if self._current_adapter != mms_lang_code:
                    self._model.load_adapter(mms_lang_code)
                    self._model.eval()
                    logger.info(
                        f"MMS adapter swapped: {self._current_adapter} → {mms_lang_code} "
                        f"(base model kept resident)"
                    )
                self._current_adapter = mms_lang_code
                self._current_lang = cache_key
                return

            from transformers import Wav2Vec2ForCTC

            logger.info(f"Loading MMS 1B-all with {language} adapter (force_mms={force_mms})")
            self._processor = self._mms_processor(mms_lang_code)
            self._model = Wav2Vec2ForCTC.from_pretrained(MMS_BASE_MODEL).to(device)
            self._model.load_adapter(mms_lang_code)
            self._model.eval()
            self._current_base = MMS_BASE_MODEL
            self._current_adapter = mms_lang_code
            logger.info(f"MMS adapter loaded: {mms_lang_code}")
        elif language in LANG_MODEL_MAP:
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

            model_name = LANG_MODEL_MAP[language]
            logger.info(f"Loading HuggingFace model: {model_name}")
            self._processor = Wav2Vec2Processor.from_pretrained(model_name)
            self._model = Wav2Vec2ForCTC.from_pretrained(model_name).to(device)  # pyright: ignore[reportArgumentType]
            self._model.eval()
            self._current_base = model_name
            self._current_adapter = None
        else:
            logger.info("Loading torchaudio MMS_FA model")
            bundle = torchaudio.pipelines.MMS_FA
            self._model = bundle.get_model(with_star=False).to(device)
            self._processor = bundle
            self._current_base = _TORCHAUDIO_MMS_FA
            self._current_adapter = None

        self._current_lang = cache_key

    def transcribe(
        self,
        audio: AudioData,
        language: str | None = None,
    ) -> TranscriptionResult:
        raise NotImplementedError(
            "CTCEngine does not support transcription. Use for forced alignment only."
        )

    def _chunk_windows(self, n_samples: int) -> list[tuple[int, int]]:
        """align_chunk_sec/overlap로 겹침 청크 윈도를 계획한다 (16kHz 기준).

        chunk_sec=0이거나 오디오가 한 청크에 들어가면 윈도 1개 → 통짜 경로(정렬 결과 불변)."""
        from everyric2.audio.chunking import plan_chunk_windows

        sr = 16000
        chunk_sec = getattr(self.config, "align_chunk_sec", 0.0) or 0.0
        overlap_sec = getattr(self.config, "align_chunk_overlap_sec", 5.0) or 0.0
        return plan_chunk_windows(n_samples, int(chunk_sec * sr), int(overlap_sec * sr))

    def _model_logits(self, waveform_1d: torch.Tensor, device) -> torch.Tensor:
        """단일 파형 청크 → wav2vec2/MMS 로짓 [1, t, V] (device 위, no-grad)."""
        with torch.inference_mode():
            inputs = self._processor(  # pyright: ignore[reportCallIssue,reportOptionalCall]
                waveform_1d.numpy(), sampling_rate=16000, return_tensors="pt", padding=True
            )
            input_values = inputs.input_values.to(device)
            return self._model(input_values).logits  # pyright: ignore[reportOptionalCall]

    def _ctc_log_emission(self, waveform: torch.Tensor, device) -> torch.Tensor:
        """CJK 경로 log_softmax emission [1, T, V].

        긴 오디오는 겹침 청크로 나눠 청크별 forward 후 CPU에서 스티칭해 피크 VRAM을 청크
        길이로 제한한다. 단일 청크면 통짜와 완전히 동일한 device 텐서를 돌려준다
        (log_softmax는 기존과 동일하게 inference_mode 밖에서 수행)."""
        n = int(waveform.shape[0])
        windows = self._chunk_windows(n)
        if len(windows) == 1:
            logits = self._model_logits(waveform, device)
            return torch.nn.functional.log_softmax(logits.float(), dim=-1)

        from everyric2.audio.chunking import stitch_chunk_outputs

        pieces = []
        for s, e in windows:
            logits = self._model_logits(waveform[s:e].contiguous(), device)
            pieces.append(torch.nn.functional.log_softmax(logits.float(), dim=-1).cpu())
            del logits
        return stitch_chunk_outputs(pieces, windows, n, frame_axis=1)

    def _mms_emission(self, waveform: torch.Tensor, device) -> torch.Tensor:
        """MMS_FA 경로 emission [1, T, V] (torchaudio 모델이 이미 log-probs 출력).

        긴 오디오는 겹침 청크로 나눠 CPU에서 스티칭. 단일 청크면 통짜와 동일한 device 텐서."""
        n = int(waveform.shape[0])
        windows = self._chunk_windows(n)
        if len(windows) == 1:
            with torch.inference_mode():
                emission, _ = self._model(waveform.unsqueeze(0).to(device))  # pyright: ignore[reportOptionalCall]
            return emission

        from everyric2.audio.chunking import stitch_chunk_outputs

        pieces = []
        for s, e in windows:
            with torch.inference_mode():
                emis, _ = self._model(  # pyright: ignore[reportOptionalCall]
                    waveform[s:e].contiguous().unsqueeze(0).to(device)
                )
            pieces.append(emis.cpu())
            del emis
        return stitch_chunk_outputs(pieces, windows, n, frame_axis=1)

    def _align_cjk(
        self,
        waveform: torch.Tensor,
        lyrics: list[LyricLine],
        language: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[SyncResult]:
        device = self._get_device()

        if progress_callback:
            progress_callback(2, 5)

        # 오디오를 겹침 청크로 나눠 청크별 forward 후 CPU에서 log_softmax emission을
        # 스티칭한다 — 통짜 forward는 인코더 활성값이 길이 비례라 긴 곡에서 OOM
        # (실사고 2026-07-24: 17분 곡). 짧은 곡·비활성은 단일 청크라 통짜와 완전히
        # 동일한 device 텐서를 돌려받는다 (align_chunk_sec).
        emission = self._ctc_log_emission(waveform, device)

        if progress_callback:
            progress_callback(3, 5)

        vocab = self._processor.tokenizer.get_vocab()  # pyright: ignore[reportAttributeAccessIssue,reportOptionalMemberAccess]

        # 가사에 없는 가창(추임새/애드립/반복 후렴)을 흡수하는 와일드카드 star 채널.
        # forced_align은 정규화된 log_probs를 기대하며, star의 0.0(=log 1.0) 트릭은
        # log_softmax 정규화 후에만 작동한다 (raw logits에서는 0이 평범한 값이라 무효 —
        # 실측 검증됨). star 없이는 정규화가 Viterbi 경로에 영향을 주지 않으므로(프레임별
        # 상수 상쇄) 기존 결과와 동일하다.
        use_star = getattr(self.config, "star_tokens", False)
        star_id = emission.shape[-1]
        if use_star:
            star_col = torch.zeros(
                (emission.shape[0], emission.shape[1], 1),
                dtype=emission.dtype,
                device=emission.device,
            )
            emission = torch.cat([emission, star_col], dim=2)

        tokens = []
        line_boundaries = []
        char_info = []
        star_positions: list[int] = []
        current_pos = 0

        if use_star:
            # 인트로(첫 라인 전) 애드립 흡수용 선행 star
            star_positions.append(current_pos)
            tokens.append(star_id)
            current_pos += 1

        for line_idx, line in enumerate(lyrics):
            line_start = current_pos
            for char in line.text:
                # 조회용 글자는 소문자화·OOV 치환을 거칠 수 있지만, char_info의 "char"는
                # 원본을 그대로 넣는다 — 표시 문자열과 역매핑 인덱스가 원문 기준이어야
                # 하고, 타이밍만 치환 글자의 정렬에서 빌려 온다.
                tok_char = _resolve_token_char(char, vocab)
                if tok_char is not None:
                    char_info.append(
                        {
                            "char": char,
                            "line_idx": line_idx,
                            "token_idx": current_pos,
                        }
                    )
                    tokens.append(vocab[tok_char])
                    current_pos += 1

            if "|" in vocab:
                tokens.append(vocab["|"])
                current_pos += 1

            if use_star:
                # 라인 사이·마지막 라인 뒤의 가사 밖 가창 흡수.
                # 일본어 MMS 어댑터는 vocab에 "|"가 없어 star가 유일한 라인 간 완충이다.
                # star span은 char_info에 없으므로 라인 시각 계산에서 자동 제외된다.
                star_positions.append(current_pos)
                tokens.append(star_id)
                current_pos += 1

            line_boundaries.append((line_start, current_pos - 1))

        if not tokens:
            raise AlignmentError("No valid tokens found in lyrics for this language")

        try:
            # emission이 청킹으로 CPU에 있으면 targets도 같은 디바이스여야 한다
            targets = torch.tensor([tokens], dtype=torch.int32, device=emission.device)
            blank_id = self._processor.tokenizer.pad_token_id  # pyright: ignore[reportAttributeAccessIssue,reportOptionalMemberAccess]
            aligned_tokens, alignment_scores = F.forced_align(emission, targets, blank=blank_id)
            token_spans = F.merge_tokens(aligned_tokens[0], alignment_scores[0], blank=blank_id)
        except Exception as e:
            raise AlignmentError(f"CTC forced alignment failed: {e}")

        if progress_callback:
            progress_callback(4, 5)

        num_frames = emission.shape[1]
        audio_length = waveform.shape[0] / 16000
        ratio = audio_length / num_frames

        # star가 실제로 흡수한 구간 기록 (1프레임=20ms짜리 형식적 흡수는 제외)
        self._last_star_spans = []
        for idx in star_positions:
            if idx < len(token_spans):
                span = token_spans[idx]
                s, e = span.start * ratio, span.end * ratio
                if e - s >= 0.1:
                    self._last_star_spans.append((round(s, 2), round(e, 2)))

        self._last_word_timestamps = []
        line_char_timestamps: dict[int, list[WordTimestamp]] = {}

        for ci in char_info:
            idx = ci["token_idx"]
            line_idx = ci["line_idx"]
            if idx < len(token_spans):
                span = token_spans[idx]
                start_time = span.start * ratio
                end_time = span.end * ratio
                # emission이 log_softmax라 span.score는 평균 로그확률(음수) — 그대로
                # 내보내면 클라이언트 신뢰도 표시가 전부 '낮음'으로 찍힌다.
                # exp로 기하평균 확률(0~1)로 변환해 저장한다.
                wt = WordTimestamp(
                    word=ci["char"],
                    start=start_time,
                    end=end_time,
                    confidence=round(math.exp(min(0.0, float(span.score))), 6),
                )
                self._last_word_timestamps.append(wt)
                if line_idx not in line_char_timestamps:
                    line_char_timestamps[line_idx] = []
                line_char_timestamps[line_idx].append(wt)

        from everyric2.inference.prompt import WordSegment

        # 1) 줄별 정렬 결과 수집 (정렬 실패 = 시각 None)
        line_times: list[list] = []
        for line_idx, line in enumerate(lyrics):
            char_ts = line_char_timestamps.get(line_idx, [])
            if char_ts:
                word_segments = [
                    WordSegment(word=wt.word, start=wt.start, end=wt.end, confidence=wt.confidence)
                    for wt in char_ts
                ]
                line_times.append([char_ts[0].start, char_ts[-1].end, word_segments])
            else:
                # OOV 등으로 정렬된 글자가 0개 → 아래에서 이웃 사이로 보간
                line_times.append([None, None, None])

        # 2) 정렬 실패 줄 보간. 균등 배치(line_idx*total/N)는 실제 정렬 시각과 뒤섞여
        #    순서가 깨지므로(역순·겹침), 앞뒤 정렬 줄 사이 간격에 끼워넣어 순서를 보존한다.
        self._interpolate_unaligned(line_times, audio_length)

        # 3) SyncResult 생성
        results = [
            SyncResult(
                line_number=line.line_number,
                text=line.text,
                start_time=line_times[i][0],
                end_time=line_times[i][1],
                word_segments=line_times[i][2],
            )
            for i, line in enumerate(lyrics)
        ]

        if progress_callback:
            progress_callback(5, 5)

        return results

    @staticmethod
    def _interpolate_unaligned(
        line_times: list[list],
        total_duration: float,
    ) -> None:
        """정렬 실패(시각 None) 줄을 앞뒤 정렬 줄 사이 간격에 균등 분배(순서 보존).

        line_times: 각 줄 [start, end, word_segments] (정렬 실패는 [None, None, None]).
        제자리에서 start/end를 채운다. 전부 실패면 전체 구간에 균등 분배.
        """
        n = len(line_times)
        i = 0
        while i < n:
            if line_times[i][0] is not None:
                i += 1
                continue
            group_start = i
            group_end = i
            while group_end < n and line_times[group_end][0] is None:
                group_end += 1
            group_end -= 1

            prev_end = line_times[group_start - 1][1] if group_start > 0 else 0.0
            if group_end < n - 1 and line_times[group_end + 1][0] is not None:
                next_start = line_times[group_end + 1][0]
            else:
                next_start = total_duration

            available = max(0.0, next_start - prev_end)
            num = group_end - group_start + 1
            seg = available / num if num else 0.0
            if seg < 0.1:  # 빈틈이 거의 없을 때도 최소 길이 보장(근사치)
                seg = 0.1
            for j in range(group_start, group_end + 1):
                off = j - group_start
                line_times[j][0] = prev_end + off * seg
                line_times[j][1] = prev_end + (off + 1) * seg
            i = group_end + 1

    def _align_mms(
        self,
        waveform: torch.Tensor,
        lyrics: list[LyricLine],
        language: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[SyncResult]:
        device = self._get_device()
        bundle = self._processor

        if progress_callback:
            progress_callback(2, 5)

        dictionary = bundle.get_dict(star=None)  # pyright: ignore[reportAttributeAccessIssue]

        all_words = []
        line_word_counts = []

        for line in lyrics:
            words = line.text.lower().split()
            cleaned_words = []
            for word in words:
                cleaned = "".join(c for c in word if c in dictionary and dictionary[c] != 0)
                if cleaned:
                    cleaned_words.append(cleaned)
                    all_words.append(cleaned)
            line_word_counts.append(len(cleaned_words))

        if not all_words:
            raise AlignmentError("No valid characters found in lyrics for MMS_FA model")

        if progress_callback:
            progress_callback(3, 5)

        # 통짜 forward는 활성값이 길이 비례라 긴 곡에서 OOM — 겹침 청크로 나눠 추론 후
        # CPU에서 emission을 스티칭한다. 짧은 곡·비활성은 단일 청크=통짜와 동일 (align_chunk_sec).
        emission = self._mms_emission(waveform, device)

        tokens = [
            dictionary[c]
            for word in all_words
            for c in word
            if c in dictionary and dictionary[c] != 0
        ]

        try:
            aligned_tokens, alignment_scores = F.forced_align(
                emission,
                torch.tensor([tokens], dtype=torch.int32, device=emission.device),
                blank=0,
            )
            token_spans = F.merge_tokens(aligned_tokens[0], alignment_scores[0])
        except Exception as e:
            raise AlignmentError(f"MMS_FA forced alignment failed: {e}")

        if progress_callback:
            progress_callback(4, 5)

        word_lengths = [len(word) for word in all_words]
        word_spans = []
        idx = 0
        for length in word_lengths:
            word_spans.append(token_spans[idx : idx + length])
            idx += length

        num_frames = emission.shape[1]
        ratio = waveform.shape[0] / num_frames / 16000

        self._last_word_timestamps = []
        for word, spans in zip(all_words, word_spans):
            if spans:
                start_time = spans[0].start * ratio
                end_time = spans[-1].end * ratio
                avg_score = sum(s.score for s in spans) / len(spans)

                self._last_word_timestamps.append(
                    WordTimestamp(
                        word=word,
                        start=start_time,
                        end=end_time,
                        confidence=avg_score,
                    )
                )

        results = []
        word_idx = 0
        audio_length = waveform.shape[0] / 16000

        for line_idx, line in enumerate(lyrics):
            word_count = line_word_counts[line_idx]

            if word_count > 0 and word_idx < len(word_spans):
                line_spans = word_spans[word_idx : word_idx + word_count]
                if line_spans and line_spans[0] and line_spans[-1]:
                    start_time = line_spans[0][0].start * ratio
                    end_time = line_spans[-1][-1].end * ratio
                else:
                    start_time = line_idx * audio_length / len(lyrics)
                    end_time = (line_idx + 1) * audio_length / len(lyrics)
                word_idx += word_count
            else:
                start_time = line_idx * audio_length / len(lyrics)
                end_time = (line_idx + 1) * audio_length / len(lyrics)

            results.append(
                SyncResult(
                    line_number=line.line_number,
                    text=line.text,
                    start_time=start_time,
                    end_time=end_time,
                )
            )

        if progress_callback:
            progress_callback(5, 5)

        return results

    def align(
        self,
        audio: AudioData,
        lyrics: list[LyricLine],
        language: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[SyncResult]:
        force_mms = False
        if language and language != "auto":
            resolved_lang = language
        else:
            lyrics_text = " ".join(line.text for line in lyrics)
            resolved_lang, is_multilingual = detect_language_from_text(lyrics_text)
            if is_multilingual:
                force_mms = True
            logger.info(f"Auto-detected language: {resolved_lang}, multilingual: {is_multilingual}")

        # 모델 로드부터 추론까지를 한 락으로 묶는다. 어댑터 교체는 상주 모델을 제자리에서
        # 바꾸므로, 다른 스레드가 forward 중일 때 끼어들면 어댑터와 가사 토큰의 언어가
        # 어긋난 채 정렬된다(max_concurrent_jobs>1 설정에서 발생 가능). 기본값 1에서는
        # 경합이 없어 비용 0이고, RLock이라 같은 스레드의 중첩 호출은 그대로 통과한다.
        with self._model_lock:
            self._ensure_model_loaded(resolved_lang, force_mms=force_mms)

            if progress_callback:
                progress_callback(1, 5)

            from everyric2.audio.loader import AudioLoader

            loader = AudioLoader()
            prepared = loader.prepare_for_alignment(audio, target_sr=16000, normalize=True)

            waveform = torch.from_numpy(prepared.waveform.astype("float32"))
            if waveform.dim() == 2:
                waveform = waveform.mean(dim=0)

            if resolved_lang in LANG_MODEL_MAP:
                results = self._align_cjk(waveform, lyrics, resolved_lang, progress_callback)
                self._last_match_stats = self._calculate_match_stats(results)
                return results
            else:
                results = self._align_mms(waveform, lyrics, resolved_lang, progress_callback)
                from everyric2.alignment.matcher import LyricsMatcher

                matcher = LyricsMatcher()
                matched_results = matcher.match_lyrics_to_words(
                    lyrics, self._last_word_timestamps, resolved_lang
                )

                self._last_match_stats = self._calculate_match_stats(matched_results)
                return matched_results

    def _calculate_match_stats(self, results: list) -> "MatchStats":
        """Calculate match stats consistently for both CJK and MMS paths.

        Uses adaptive threshold: > 0 for positive confidence (CJK),
        > -5 for log-probability confidence (MMS/English).
        """
        from everyric2.alignment.matcher import MatchStats

        all_confidences = [
            seg.confidence
            for r in results
            if r.word_segments
            for seg in r.word_segments
            if seg.confidence is not None
        ]

        if not all_confidences:
            return MatchStats(
                total_lyrics=len(results),
                matched_lyrics=0,
                match_rate=0.0,
                avg_confidence=0.0,
            )

        avg_conf = sum(all_confidences) / len(all_confidences)

        # Adaptive threshold: log-probs are negative, CTC scores can be positive
        # For log-probs (avg < 0): use -5 as "good enough" threshold
        # For CTC scores (avg >= 0): use 0 as threshold
        threshold = -5.0 if avg_conf < 0 else 0.0
        good_matches = sum(1 for c in all_confidences if c > threshold)
        match_rate = good_matches / len(all_confidences)

        return MatchStats(
            total_lyrics=len(results),
            matched_lyrics=sum(1 for r in results if r.word_segments),
            match_rate=match_rate,
            avg_confidence=avg_conf,
        )

    def get_last_transcription_data(self) -> tuple[list[WordTimestamp], MatchStats | None, str]:
        return (self._last_word_timestamps, self._last_match_stats, "ctc")

    def get_transcription_sets(self) -> list[tuple[list[WordTimestamp], MatchStats | None, str]]:
        data = self.get_last_transcription_data()
        if data[0]:
            return [data]
        return []

    @staticmethod
    def get_engine_type() -> Literal["ctc"]:
        return "ctc"


# 웜 캐시 싱글턴 (WS2-A) — 프로세스 수명 동안 CTC 엔진(과 그 안에 lazy 로드된 wav2vec2/MMS
# 모델)을 상주시킨다. torch를 최상위에서 import하는 모듈이라, 이 접근자는 반드시 호출부에서
# 지연 import해야 API 전용 모드(local_worker=false)에 torch가 딸려 들어오지 않는다.
_shared_ctc_engine: "CTCEngine | None" = None
_shared_ctc_lock = threading.Lock()


def get_shared_ctc_engine(config: AlignmentSettings | None = None) -> "CTCEngine":
    """웜 캐시된 CTCEngine을 돌려준다 (EVERYRIC_SERVER_WARM_MODELS 기준).

    엔진 인스턴스는 _ensure_model_loaded가 로드한 모델을 _current_lang로 캐시하므로, 같은
    엔진을 재사용하면 같은 언어의 두 번째 잡부터 모델 재로드가 0회다. 재사용 시 "warm model
    reuse: ctc" 1줄. warm이 꺼져 있으면 매번 새 엔진(기존 동작)."""
    from everyric2.config.settings import get_settings

    if not get_settings().server.warm_models:
        return CTCEngine(config)
    global _shared_ctc_engine
    with _shared_ctc_lock:
        if _shared_ctc_engine is None:
            _shared_ctc_engine = CTCEngine(config)
        else:
            logger.info("warm model reuse: ctc")
        return _shared_ctc_engine


def clear_shared_ctc_engine() -> None:
    """웜 캐시 해제 (VRAM 가드용) — 다음 요청에서 지연 재생성된다."""
    global _shared_ctc_engine
    with _shared_ctc_lock:
        _shared_ctc_engine = None
