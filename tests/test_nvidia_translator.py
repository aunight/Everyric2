"""Tests for the NVIDIA NIM translation engine and the en/ko pronunciation gate."""

from dataclasses import dataclass

import pytest

from everyric2.config.settings import TranslationSettings
from everyric2.translation.translator import (
    BaseTranslator,
    GeminiTranslator,
    NvidiaTranslator,
    OpenAICompatibleTranslator,
    TranslatorFactory,
)


@dataclass
class FakeResponse:
    status_code: int
    _payload: dict
    ok: bool = True
    text: str = ""

    def json(self):
        return self._payload


def chat_response(content: str, finish_reason: str | None = None) -> FakeResponse:
    return FakeResponse(
        status_code=200,
        _payload={
            "choices": [{"message": {"content": content}, "finish_reason": finish_reason}]
        },
    )


class TestTranslatorFactory:
    def test_nvidia_engine_returns_nvidia_translator(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        settings = TranslationSettings(engine="nvidia", api_key="dummy-key")
        translator = TranslatorFactory.get_translator(settings)

        assert isinstance(translator, NvidiaTranslator)
        assert isinstance(translator, OpenAICompatibleTranslator)
        assert translator.api_url == NvidiaTranslator.NIM_API_URL

    def test_nvidia_uses_nvidia_model_field_not_generic_model(self, monkeypatch):
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        settings = TranslationSettings(
            engine="nvidia", api_key="dummy-key", model="gemini-2.0-flash"
        )
        translator = TranslatorFactory.get_translator(settings)

        assert translator.model == settings.nvidia_model
        assert translator.model != "gemini-2.0-flash"

    def test_gemini_engine_without_key_auto_switches_to_nvidia(self, monkeypatch, tmp_path):
        # gemini 키가 없으면 웹 폴백(발음 불가)으로 격하되는 대신 NIM 키가 있으면 NIM으로
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("nim-key", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

        translator = TranslatorFactory.get_translator(
            TranslationSettings(engine="gemini", api_key=None)
        )
        assert isinstance(translator, NvidiaTranslator)

    def test_gemini_engine_with_key_stays_gemini(self, monkeypatch, tmp_path):
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("nim-key", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.setenv("GEMINI_API_KEY", "gm-key")

        translator = TranslatorFactory.get_translator(
            TranslationSettings(engine="gemini", api_key=None)
        )
        assert isinstance(translator, GeminiTranslator)

    def test_gemini_engine_without_any_key_keeps_gemini_web_fallback(self, monkeypatch, tmp_path):
        # NIM 키도 없으면 기존 동작(웹 번역 폴백) 유지 — 번역이라도 나가야 한다
        missing = tmp_path / "does_not_exist.txt"
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", missing)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

        translator = TranslatorFactory.get_translator(
            TranslationSettings(engine="gemini", api_key=None)
        )
        assert isinstance(translator, GeminiTranslator)


class TestApiKeyResolutionOrder:
    def test_settings_api_key_wins(self, monkeypatch, tmp_path):
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("file-key\n", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.setenv("NVIDIA_API_KEY", "env-key")

        settings = TranslationSettings(engine="nvidia", api_key="settings-key")
        translator = NvidiaTranslator(settings)

        assert translator.api_key == "settings-key"

    def test_env_var_wins_over_key_file(self, monkeypatch, tmp_path):
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("file-key\n", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.setenv("NVIDIA_API_KEY", "env-key")

        settings = TranslationSettings(engine="nvidia", api_key=None)
        translator = NvidiaTranslator(settings)

        assert translator.api_key == "env-key"

    def test_falls_back_to_key_file(self, monkeypatch, tmp_path):
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("  file-key-with-whitespace  \n", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

        settings = TranslationSettings(engine="nvidia", api_key=None)
        translator = NvidiaTranslator(settings)

        assert translator.api_key == "file-key-with-whitespace"

    def test_missing_key_file_yields_none(self, monkeypatch, tmp_path):
        missing_file = tmp_path / "does_not_exist.txt"
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", missing_file)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

        settings = TranslationSettings(engine="nvidia", api_key=None)
        translator = NvidiaTranslator(settings)

        assert translator.api_key is None


class TestPronunciationGateHeuristic:
    """BaseTranslator._should_skip_pronunciation / _detect_lang_heuristic."""

    def setup_method(self):
        class _Probe(BaseTranslator):
            def translate(self, *a, **k):  # pragma: no cover - not exercised
                raise NotImplementedError

        self.probe = _Probe(TranslationSettings())

    @pytest.mark.parametrize(
        "source_lang,text,expected",
        [
            ("en", "I can hear your voice", True),
            ("ko", "오늘 밤 너의 목소리", True),
            ("ja", "きみの声が聴こえる", False),
            ("zh", "我听到你的声音", False),
        ],
    )
    def test_explicit_source_lang(self, source_lang, text, expected):
        assert self.probe._should_skip_pronunciation(text, source_lang) is expected

    def test_auto_detects_english(self):
        text = "Walking down an empty street tonight"
        assert self.probe._detect_lang_heuristic(text) == "en"
        assert self.probe._should_skip_pronunciation(text, "auto") is True

    def test_auto_detects_korean(self):
        text = "오늘 밤 너의 목소리가 들려"
        assert self.probe._detect_lang_heuristic(text) == "ko"
        assert self.probe._should_skip_pronunciation(text, "auto") is True

    def test_auto_detects_japanese_as_other(self):
        text = "夜の街に消えていく光"
        assert self.probe._detect_lang_heuristic(text) == "other"
        assert self.probe._should_skip_pronunciation(text, "auto") is False


class TestPayloadExtras:
    """reasoning 모델별 추가 페이로드 — qwen은 thinking off, gpt-oss는 effort low.
    안 보내면 사고가 max_tokens 예산을 소진해 빈 응답/잘린 JSON이 난다."""

    def _make(self, model: str) -> NvidiaTranslator:
        settings = TranslationSettings(
            engine="nvidia", api_key="dummy-key", nvidia_model=model
        )
        return NvidiaTranslator(settings)

    def test_qwen_disables_thinking(self):
        extras = self._make("qwen/qwen3-next-80b-a3b-instruct")._payload_extras()
        assert extras == {"chat_template_kwargs": {"thinking": False}}

    def test_gpt_oss_uses_low_reasoning_effort(self):
        extras = self._make("openai/gpt-oss-120b")._payload_extras()
        assert extras == {"reasoning_effort": "low"}

    def test_default_model_is_covered_by_extras(self):
        # 기본 모델이 reasoning 계열로 바뀌면 extras 분기도 함께 따라와야 한다
        settings = TranslationSettings(engine="nvidia", api_key="dummy-key")
        extras = NvidiaTranslator(settings)._payload_extras()
        assert extras == {"reasoning_effort": "low"}

    def test_other_models_send_no_extras(self):
        assert self._make("mistralai/mistral-large")._payload_extras() == {}


class TestTranslateAppliesGate:
    """The gate must apply inside translate(), overriding settings.include_pronunciation,
    and the request payload must always include max_tokens (NIM truncates long
    pronunciation JSON without it)."""

    def _make_translator(self, monkeypatch, tmp_path, include_pronunciation=True):
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("dummy-key", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        settings = TranslationSettings(engine="nvidia", api_key=None)
        settings.include_pronunciation = include_pronunciation
        return NvidiaTranslator(settings)

    def test_english_source_skips_pronunciation_even_if_requested(self, monkeypatch, tmp_path):
        translator = self._make_translator(monkeypatch, tmp_path, include_pronunciation=True)

        captured = {}

        def fake_post(url, json, headers, timeout):
            captured["json"] = json
            return chat_response("Hello there\nGood morning")

        monkeypatch.setattr(
            "everyric2.translation.translator.requests.post", fake_post
        )

        result = translator.translate(
            "안녕하세요\n좋은 아침입니다",
            source_lang="ko",
            target_lang="en",
        )

        assert all(line.pronunciation is None for line in result.lines)
        assert captured["json"]["max_tokens"] == translator.settings.max_tokens
        # plain-text prompt path was used, not the JSON pronunciation format
        assert "pronunciation" not in captured["json"]["messages"][0]["content"].lower() or (
            "romanized" not in captured["json"]["messages"][0]["content"].lower()
        )

    def test_japanese_source_keeps_pronunciation_and_parses_json(self, monkeypatch, tmp_path):
        translator = self._make_translator(monkeypatch, tmp_path, include_pronunciation=True)

        def fake_post(url, json, headers, timeout):
            content = (
                '[{"original": "おはよう", '
                '"translation": "안녕", '
                '"pronunciation": "Ohayou"}]'
            )
            return chat_response(content)

        monkeypatch.setattr(
            "everyric2.translation.translator.requests.post", fake_post
        )

        result = translator.translate("おはよう", source_lang="ja", target_lang="ko")

        assert len(result.lines) == 1
        assert result.lines[0].pronunciation == "Ohayou"
        assert result.lines[0].translation == "안녕"
        assert result.engine == "nvidia"


class TestTruncatedJsonRecovery:
    """NIM 발음 JSON이 max_tokens에서 잘렸을 때의 복구/재분할/폴백.

    ① 잘린 JSON에서 완전한 앞 객체까지 살려낸다
    ② 못 받은 나머지 라인만 재요청한다(진전 없으면 절반 분할)
    ③ 그래도 실패한 라인은 원문만 담고 failed=True로 반환 — 전체 500을 막는다
    실제 NIM API는 호출하지 않는다(requests.post를 mock).
    """

    def _make_translator(self, monkeypatch, tmp_path):
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("dummy-key", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        settings = TranslationSettings(engine="nvidia", api_key=None)
        settings.include_pronunciation = True
        return NvidiaTranslator(settings)

    def _sequence_post(self, monkeypatch, responses):
        """호출 순서대로 미리 정한 FakeResponse를 돌려주는 requests.post 대체.
        보낸 각 요청의 payload를 calls에 기록한다."""
        calls = []
        it = iter(responses)

        def fake_post(url, json, headers, timeout):
            calls.append(json)
            return next(it)

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)
        return calls

    def _obj(self, orig, trans, pron):
        return f'{{"original":"{orig}","translation":"{trans}","pronunciation":"{pron}"}}'

    def test_salvages_complete_prefix_then_requests_remainder(self, monkeypatch, tmp_path):
        translator = self._make_translator(monkeypatch, tmp_path)

        # 1차: 3줄 요청인데 2번째 객체까지만 완성되고 3번째에서 끊김(length)
        truncated = (
            "["
            + self._obj("ライン1", "번역1", "ぷろんいち")
            + ","
            + self._obj("ライン2", "번역2", "ぷろんに")
            + ',{"original":"ライン3","translation":"번역'  # 잘림
        )
        # 2차(나머지 1줄): 정상 완결
        remainder = "[" + self._obj("ライン3", "번역3", "ぷろんさん") + "]"

        calls = self._sequence_post(
            monkeypatch,
            [chat_response(truncated, finish_reason="length"), chat_response(remainder, "stop")],
        )

        result = translator.translate("ライン1\nライン2\nライン3", source_lang="ja", target_lang="ko")

        assert len(result.lines) == 3
        assert [l.translation for l in result.lines] == ["번역1", "번역2", "번역3"]
        assert [l.pronunciation for l in result.lines] == ["ぷろんいち", "ぷろんに", "ぷろんさん"]
        assert all(not l.failed for l in result.lines)
        # 나머지 재요청은 3번째 라인만 담았어야 한다(전체 재요청 아님)
        assert len(calls) == 2
        assert "ライン3" in calls[1]["messages"][0]["content"]
        assert "ライン1" not in calls[1]["messages"][0]["content"]

    def test_splits_in_half_when_no_object_salvageable(self, monkeypatch, tmp_path):
        translator = self._make_translator(monkeypatch, tmp_path)

        # 1차: 첫 객체조차 완성 못한 채 끊김 → 살릴 객체 0 → 절반 분할
        truncated = '[{"original":"アア","translation":"번'
        left = "[" + self._obj("アア", "왼1", "ひだりいち") + "," + self._obj("イイ", "왼2", "ひだりに") + "]"
        right = "[" + self._obj("ウウ", "오1", "みぎいち") + "," + self._obj("エエ", "오2", "みぎに") + "]"

        calls = self._sequence_post(
            monkeypatch,
            [
                chat_response(truncated, finish_reason="length"),
                chat_response(left, "stop"),
                chat_response(right, "stop"),
            ],
        )

        result = translator.translate(
            "アア\nイイ\nウウ\nエエ", source_lang="ja", target_lang="ko"
        )

        assert len(result.lines) == 4
        assert [l.translation for l in result.lines] == ["왼1", "왼2", "오1", "오2"]
        assert all(not l.failed for l in result.lines)
        assert len(calls) == 3  # 원본 1 + 좌/우 2

    def test_unrecoverable_line_falls_back_to_original_only(self, monkeypatch, tmp_path):
        translator = self._make_translator(monkeypatch, tmp_path)

        # 1차: 1번째만 완성, 2번째 잘림 → 나머지(2번째) 재요청
        truncated = "[" + self._obj("サキ", "성공1", "せいこう") + ',{"original":"ダメ","trans'
        # 2차(2번째 라인 단독): 또 잘려서 살릴 객체 0 → 단일 라인이라 폴백
        still_bad = '[{"original":"ダメ","transl'

        calls = self._sequence_post(
            monkeypatch,
            [
                chat_response(truncated, finish_reason="length"),
                chat_response(still_bad, finish_reason="length"),
            ],
        )

        result = translator.translate("サキ\nダメ", source_lang="ja", target_lang="ko")

        # 전체 500이 아니라 부분 성공으로 마감 — 줄 수는 보존
        assert len(result.lines) == 2
        assert result.lines[0].translation == "성공1"
        assert result.lines[0].failed is False
        # 복구 불가 라인: 원문만, translation/pron 비움, failed 표시
        assert result.lines[1].original == "ダメ"
        assert result.lines[1].translation == ""
        assert result.lines[1].pronunciation is None
        assert result.lines[1].failed is True

    def test_empty_completion_does_not_500_and_marks_failed(self, monkeypatch, tmp_path):
        # reasoning이 max_tokens를 다 써 content가 빈 응답 — 재시도 후에도 비면
        # 단일 라인은 폴백(failed)로, 전체 예외는 나지 않는다
        translator = self._make_translator(monkeypatch, tmp_path)
        self._sequence_post(
            monkeypatch,
            [chat_response("", finish_reason="length"), chat_response("", "length")],
        )

        result = translator.translate("ヒトリ", source_lang="ja", target_lang="ko")

        assert len(result.lines) == 1
        assert result.lines[0].original == "ヒトリ"
        assert result.lines[0].failed is True

    def test_long_input_is_batched_up_front(self, monkeypatch, tmp_path):
        # 라인 수가 임계를 넘으면 처음부터 배치로 나눠 요청(잘림 예방)
        import everyric2.translation.translator as tr

        monkeypatch.setattr(tr, "_PRON_BATCH_THRESHOLD", 2)
        monkeypatch.setattr(tr, "_PRON_BATCH_SIZE", 2)
        translator = self._make_translator(monkeypatch, tmp_path)

        def fake_post(url, json, headers, timeout):
            # 요청에 담긴 원문 라인들에 맞춰 정상 JSON을 만들어 돌려준다
            content = json["messages"][0]["content"]
            lines = content.split("LYRICS:\n")[-1].strip().split("\n")
            arr = ",".join(self._obj(ln, f"t-{ln}", f"p-{ln}") for ln in lines)
            return chat_response("[" + arr + "]", "stop")

        calls = []
        orig = fake_post

        def counting_post(url, json, headers, timeout):
            calls.append(json)
            return orig(url, json, headers, timeout)

        monkeypatch.setattr("everyric2.translation.translator.requests.post", counting_post)

        result = translator.translate(
            "ア\nイ\nウ\nエ\nオ", source_lang="ja", target_lang="ko"
        )

        assert len(result.lines) == 5
        assert [l.translation for l in result.lines] == ["t-ア", "t-イ", "t-ウ", "t-エ", "t-オ"]
        # 임계 2, 배치 2 → 5줄은 3번 요청(2+2+1)
        assert len(calls) == 3


class TestSalvageJsonHelper:
    """_salvage_json_lines / _decode_json_objects 단위 검증(요청 없이)."""

    def _probe(self):
        settings = TranslationSettings(engine="nvidia", api_key="dummy-key")
        return NvidiaTranslator(settings)

    def test_full_array_parses_all(self):
        p = self._probe()
        text = (
            '[{"original":"a","translation":"A","pronunciation":"aa"},'
            '{"original":"b","translation":"B","pronunciation":"bb"}]'
        )
        lines = p._salvage_json_lines(text, ["a", "b"])
        assert [l.translation for l in lines] == ["A", "B"]

    def test_truncated_array_keeps_complete_prefix(self):
        p = self._probe()
        text = '[{"original":"a","translation":"A","pronunciation":"aa"},{"original":"b","transl'
        lines = p._salvage_json_lines(text, ["a", "b"])
        assert len(lines) == 1
        assert lines[0].translation == "A"

    def test_code_fenced_and_think_wrapped(self):
        p = self._probe()
        text = (
            "<think>reasoning...</think>\n```json\n"
            '[{"original":"x","translation":"X","pronunciation":"xx"}]\n```'
        )
        lines = p._salvage_json_lines(text, ["x"])
        assert len(lines) == 1
        assert lines[0].pronunciation == "xx"

    def test_no_array_returns_empty(self):
        p = self._probe()
        assert p._salvage_json_lines("sorry, I cannot help", ["a"]) == []
        assert p._salvage_json_lines("", ["a"]) == []

    def test_original_falls_back_to_input_line_when_missing(self):
        p = self._probe()
        text = '[{"translation":"A","pronunciation":"aa"}]'
        lines = p._salvage_json_lines(text, ["原文"])
        assert lines[0].original == "原文"


class TestPromptBuilding:
    """_build_prompt — ko 타깃은 한글 독음, 곡 컨텍스트 주입, 가사 맥락 지시."""

    def setup_method(self):
        class _Probe(BaseTranslator):
            def translate(self, *a, **k):  # pragma: no cover - not exercised
                raise NotImplementedError

        self.probe = _Probe(TranslationSettings())

    def test_ko_target_pron_asks_kana_reading_not_romanization(self):
        # 새 계약: LLM은 가나 독음만 쓰고(문맥 한자 읽기), 한글 변환은 서버가 한다
        # (kana_hangul — 촉음/ん/장음의 기계 전사 실수 원천 차단)
        prompt = self.probe._build_prompt("時計の針が", "ja", "ko", include_pronunciation=True)
        assert "kana reading" in prompt
        # 가나 예시가 있어야 LLM이 로마자/한글로 새지 않는다
        assert "とけいの はりが" in prompt
        assert "never Hangul" in prompt
        assert "Romanized pronunciation" not in prompt

    def test_non_ko_target_pron_stays_romanized(self):
        prompt = self.probe._build_prompt("時計の針が", "ja", "en", include_pronunciation=True)
        assert "Romanized pronunciation" in prompt
        assert "kana reading" not in prompt

    def test_song_context_is_injected(self):
        prompt = self.probe._build_prompt(
            "きみの声", "ja", "ko", include_pronunciation=False, context='"熱異常" by かいりきベア'
        )
        assert 'Song: "熱異常" by かいりきベア' in prompt

    def test_lyrics_guidance_present_in_both_paths(self):
        for pron in (True, False):
            prompt = self.probe._build_prompt("きみの声", "ja", "ko", include_pronunciation=pron)
            assert "ONE song" in prompt
