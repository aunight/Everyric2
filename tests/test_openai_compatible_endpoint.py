"""engine="openai" + api_url = 임의의 OpenAI 호환 제공자.

중국어 모델(DeepSeek·Qwen·GLM)은 전부 OpenAI chat/completions 규격이라 별도 엔진 클래스가
필요 없다. 예전에는 engine="openai"가 api.openai.com을 **하드코딩**해서, 다른 제공자를
붙이려면 engine을 "local"로 위장해야 했다(이름과 실제가 어긋나 설정을 읽는 사람이 오해한다).
api_url이 주어지면 그것을 쓴다는 계약을 여기서 고정한다 — 기본값(미지정 시 api.openai.com)도
같이 지킨다.
"""

from everyric2.config.settings import TranslationSettings
from everyric2.translation.translator import OpenAICompatibleTranslator


def test_api_url_overrides_openai_default():
    settings = TranslationSettings(
        engine="openai",
        api_url="https://api.deepseek.com/v1/chat/completions",
        model="deepseek-chat",
    )
    assert OpenAICompatibleTranslator(settings).api_url == (
        "https://api.deepseek.com/v1/chat/completions"
    )


def test_openai_without_api_url_still_defaults_to_openai():
    settings = TranslationSettings(engine="openai", api_url=None)
    assert (
        OpenAICompatibleTranslator(settings).api_url
        == "https://api.openai.com/v1/chat/completions"
    )


def test_local_engine_still_defaults_to_ollama():
    settings = TranslationSettings(engine="local", api_url=None)
    assert (
        OpenAICompatibleTranslator(settings).api_url
        == "http://localhost:11434/v1/chat/completions"
    )
