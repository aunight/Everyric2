"""zh(繁體中文·台灣) 대상 언어 계약.

zh는 코드상 언어 코드 하나('zh')로만 존재하고 **항상 대만 정체자**를 뜻한다 —
zh-TW/zh-CN을 따로 두지 않는 대신 프롬프트 지시(REGISTER_HINTS['zh'])와 웹 번역 폴백의
언어 코드 매핑, 두 곳이 그 계약을 지킨다. 여기가 깨지면 중문 사용자에게 간체자가 나가고
아무 예외도 뜨지 않아 눈으로만 잡히므로 테스트로 고정한다.
"""

import pytest

from everyric2.config.settings import TranslationSettings
from everyric2.server.db.repository import layer_content_lang_mismatch
from everyric2.translation.translator import BaseTranslator, GeminiTranslator


class _Probe(BaseTranslator):
    def translate(self, *a, **k):  # pragma: no cover - 프롬프트만 본다
        raise NotImplementedError


@pytest.fixture
def probe() -> _Probe:
    return _Probe(TranslationSettings())


class TestZhPrompt:
    def test_target_name_is_traditional_taiwan(self, probe):
        prompt = probe._build_prompt(["歌詞"], "ja", "zh", include_pronunciation=False)
        assert "Traditional Chinese (Taiwan)" in prompt

    def test_register_hint_forbids_simplified(self, probe):
        prompt = probe._build_prompt(["歌詞"], "ja", "zh", include_pronunciation=False)
        assert "never Simplified characters" in prompt
        assert "繁體中文" in prompt

    def test_ko_register_hint_not_leaked_into_zh(self, probe):
        """REGISTER_HINTS 도입 전 ko 전용 삼항식이던 자리 — 언어별로 갈리는지 확인."""
        zh = probe._build_prompt(["歌詞"], "ja", "zh", include_pronunciation=False)
        ko = probe._build_prompt(["歌詞"], "ja", "ko", include_pronunciation=False)
        assert "반말" in ko and "반말" not in zh
        assert "繁體中文" in zh and "繁體中文" not in ko

    def test_en_target_has_no_register_hint(self, probe):
        prompt = probe._build_prompt(["歌詞"], "ja", "en", include_pronunciation=False)
        assert "반말" not in prompt and "繁體中文" not in prompt


class TestZhFallbackLangCode:
    def test_google_fallback_uses_zh_tw(self, monkeypatch):
        """웹 번역 폴백이 zh-CN(간체)으로 가면 계약이 조용히 깨진다."""
        seen: dict[str, str] = {}

        class _FakeGoogle:
            def __init__(self, source: str, target: str):
                seen["target"] = target

            def translate(self, text: str) -> str:
                return "\n".join("譯" for _ in text.split("\n"))

        import deep_translator

        monkeypatch.setattr(deep_translator, "GoogleTranslator", _FakeGoogle)
        gemini = GeminiTranslator(TranslationSettings())
        result = gemini._fallback_result(["原文1", "原文2"], "ja", "zh")

        assert seen["target"] == "zh-TW"
        assert [line.translation for line in result.lines] == ["譯", "譯"]


class TestZhLayerGuard:
    def test_hangul_body_rejected_under_zh_label(self):
        """ko 기본값 폴백으로 한국어 본문이 zh 레이어에 저장되던 사고 경로를 막는다."""
        lines = [{"translation": "한국어 번역이 여기 잔뜩 들어가 있습니다 정말로"}]
        assert layer_content_lang_mismatch("zh", lines) is True

    def test_chinese_body_accepted_under_zh_label(self):
        lines = [{"translation": "這是一段正常的繁體中文歌詞翻譯，長度足夠通過判定門檻"}]
        assert layer_content_lang_mismatch("zh", lines) is False


class TestTitleGuard:
    """곡 제목 라인은 번역하지 않는다 — api/translate의 결정론 가드 (google-web 폴백 유일 방어)."""

    def test_title_line_translation_erased(self):
        from everyric2.server.api.translate import _norm_title

        assert _norm_title("夜に駆ける") == _norm_title(" 夜に駆ける ")
        assert _norm_title("Yoru ni Kakeru") == _norm_title("yoru  ni kakeru")
        assert _norm_title("夜に駆ける") != _norm_title("夜に")


class TestZhPronMatrix:
    """zh 타깃 발음은 en과 같은 로마자 계약 — lib/lang.ts resolveScript(zh→romaji)와 한 쌍."""

    def test_ja_source_zh_behaves_like_en(self, probe):
        text = "夜に駆ける"
        fn_en = probe._deterministic_pron_fn(text, "ja", "en")
        fn_zh = probe._deterministic_pron_fn(text, "ja", "zh")
        # 형태소 분석기 유무와 무관하게 두 타깃의 동작은 항상 같아야 한다
        assert (fn_en is None) == (fn_zh is None)
        if fn_en is not None:
            assert fn_zh(text) == fn_en(text)

    def test_ko_source_zh_is_romaja(self, probe):
        fn = probe._deterministic_pron_fn("사랑해", "ko", "zh")
        assert fn is not None
        assert fn("사랑해") == probe._deterministic_pron_fn("사랑해", "ko", "en")("사랑해")


class TestGeminiRateLimitFallback:
    """429가 재시도로도 안 풀리면 web 폴백으로 내려간다 — 500(번역 실패)을 돌려주지 않는다."""

    def test_persistent_429_falls_back_to_web(self, monkeypatch):
        import requests as requests_mod

        from everyric2.translation import translator as tr

        class _R:
            ok = False
            status_code = 429
            text = "quota"

        monkeypatch.setattr(requests_mod, "post", lambda *a, **k: _R())
        monkeypatch.setattr(tr.time, "sleep", lambda s: None)

        class _FakeGoogle:
            def __init__(self, source, target): pass
            def translate(self, text): return "\n".join("譯" for _ in text.split("\n"))

        import deep_translator

        monkeypatch.setattr(deep_translator, "GoogleTranslator", _FakeGoogle)

        settings = TranslationSettings(api_key="test-key", rate_limit_retries=2)
        result = GeminiTranslator(settings).translate("原文A\n原文B", "ja", "zh")
        assert result.engine == "google-web"
        assert [line.translation for line in result.lines] == ["譯", "譯"]
