"""매트릭스 대각선(Task 10) — 곡 언어 == 사용자(target) 언어면 번역·발음 표기를 생략한다.

`_should_skip_pronunciation(text, source_lang, target_lang)`이 계약이다: 곡 언어와
target_lang이 같으면(ko곡×ko유저, en곡×en유저, ja곡×ja유저 — ja×ja는 가나가 있어도)
무조건 True. 기존 "en/ko 원문이면 발음 생략" 규칙(가나 예외 포함)은 **target=ko일 때만**
유지한다 — target이 ko가 아니면 원문 언어만으로 생략을 결정하지 않는다(ko_reading 같은
미래의 비ko 발음 경로를 이 게이트가 먼저 죽이지 않게 하려고).

번역 자체의 대각선 스킵(`_should_skip_translation`, translation_skipped)은 이미 있던
기능이다 — ko곡×ko 요청이 실제로 LLM을 부르지 않고 스킵되는지 엔드투엔드로 확인한다.
"""

import pytest

from everyric2.config.settings import TranslationSettings
from everyric2.translation.translator import BaseTranslator, NvidiaTranslator


class _Probe(BaseTranslator):
    def translate(self, *a, **k):  # pragma: no cover - not exercised
        raise NotImplementedError


@pytest.fixture
def probe() -> _Probe:
    return _Probe(TranslationSettings())


class TestPronunciationMatrixDiagonal:
    """곡 언어 == target_lang → 발음 생략 (matrix 대각선)."""

    def test_ja_song_ja_target_skips_even_with_kana(self, probe):
        # ja×ja는 이 함수 전체에서 유일하게 "가나가 있어도" 생략되는 경우다 — target이 ja면
        # 가나 자체가 원문이라 "가나 독음"이 무의미하다.
        assert probe._should_skip_pronunciation("きみの声が聴こえる", "ja", "ja") is True

    def test_ko_song_ko_target_skips(self, probe):
        assert probe._should_skip_pronunciation("오늘 밤 우리 함께 걸어요", "ko", "ko") is True

    def test_en_song_en_target_skips(self, probe):
        assert probe._should_skip_pronunciation(
            "hello world this is a song about love", "en", "en"
        ) is True

    def test_ja_song_en_target_does_not_skip(self, probe):
        # ja곡×en유저 — 로마자 발음이 필요하다. target != ko이므로 구 규칙도 적용되지 않는다.
        assert probe._should_skip_pronunciation("きみの声が聴こえる", "ja", "en") is False

    def test_ja_song_ko_target_still_does_not_skip(self, probe):
        # 기존 주 경로(ja곡×ko유저)는 그대로 보존 — 한글 독음이 필요하다.
        assert probe._should_skip_pronunciation("きみの声が聴こえる", "ja", "ko") is False


class TestOldRuleScopedToKoTarget:
    """기존 "en/ko 원문이면 생략" 규칙(가나 예외 포함)은 target=ko 전용이다."""

    def test_english_source_ko_target_skips(self, probe):
        assert probe._should_skip_pronunciation("hello world", "en", "ko") is True

    def test_korean_source_ko_target_skips(self, probe):
        assert probe._should_skip_pronunciation("오늘 밤 우리 함께 걸어요", "ko", "ko") is True

    def test_english_source_en_target_is_diagonal_not_old_rule(self, probe):
        # en×en은 대각선으로 True가 나온다(구 규칙과 결과는 같지만 경로가 다르다는 것을
        # 아래 non-ko 테스트가 보인다)
        assert probe._should_skip_pronunciation("hello world", "en", "en") is True

    def test_korean_source_non_ko_target_does_not_skip(self, probe):
        # 구 규칙이 더 이상 적용되지 않는다 — target이 ko가 아니므로 ko 원문이라는 사실만으로
        # 생략하지 않는다(미래의 ko_reading 로마자 경로를 이 게이트가 막지 않게 한다).
        assert probe._should_skip_pronunciation("오늘 밤 우리 함께 걸어요", "ko", "en") is False

    def test_kana_exception_still_applies_for_ko_target(self, probe):
        # 라틴이 많아 heuristic이 en으로 오판해도 가나가 있으면(target=ko) 생략하지 않는다 —
        # 기존 실측 회귀(라틴 7줄/일본어 3줄 곡에서 발음이 전부 날아갔던 사고)의 재확인.
        text = "\n".join([
            "Approved Approved Approved Approved",
            "ひらひら numb numb",
            "おまえはATM",
            "Catch my heart",
        ])
        assert probe._detect_lang_heuristic(text) == "en"
        assert probe._should_skip_pronunciation(text, "auto", "ko") is False

    def test_kana_exception_does_not_apply_for_non_ko_target(self, probe):
        # 가나 예외 자체가 target=ko 전용 규칙 안에 있으므로, target이 en이면 애초에
        # (대각선이 아닌 이상) 그 규칙 분기에 들어가지 않는다 — ja 원문이 명시되면
        # 대각선도 아니라(ja != en) 생략하지 않는다.
        assert probe._should_skip_pronunciation("ひらひら numb おまえはATM", "ja", "en") is False


class TestTranslationDiagonalEndToEnd:
    """같은 언어 번역 스킵(translation_skipped)은 이미 있던 기능 — ko곡×ko 요청에서
    실제로 LLM을 부르지 않고 동작하는지 엔드투엔드로 확인한다."""

    def _translator(self, monkeypatch, tmp_path, include_pronunciation=True):
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("dummy-key", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        settings = TranslationSettings(engine="nvidia", api_key=None)
        settings.include_pronunciation = include_pronunciation
        return NvidiaTranslator(settings)

    def test_ko_song_ko_target_skips_translation_without_calling_llm(self, monkeypatch, tmp_path):
        translator = self._translator(monkeypatch, tmp_path, include_pronunciation=True)

        def boom(*a, **k):  # pragma: no cover - 호출되면 테스트 실패
            raise AssertionError("LLM must not be called for the ko x ko diagonal")

        monkeypatch.setattr("everyric2.translation.translator.requests.post", boom)

        result = translator.translate(
            "저기요 제가요 가슴이 떨려서\n말도 제대로 못 하고 서 있어요",
            source_lang="ko",
            target_lang="ko",
        )

        assert result.translation_skipped is True
        assert all(line.translation == "" for line in result.lines)
        assert all(line.pronunciation is None for line in result.lines)

    def test_ja_song_ja_target_skips_translation_and_pronunciation(self, monkeypatch, tmp_path):
        # 계획 Task 10 시나리오: (ja곡, target=ja) → 발음 스킵 True · translation_skipped True
        translator = self._translator(monkeypatch, tmp_path, include_pronunciation=True)

        def boom(*a, **k):  # pragma: no cover
            raise AssertionError("LLM must not be called for the ja x ja diagonal")

        monkeypatch.setattr("everyric2.translation.translator.requests.post", boom)

        result = translator.translate(
            "きみの声が聴こえる\n夜の街に消えていく光", source_lang="ja", target_lang="ja"
        )

        assert result.translation_skipped is True
        assert all(line.translation == "" for line in result.lines)
        assert all(line.pronunciation is None for line in result.lines)

    def test_ja_song_en_target_still_calls_llm_for_translation(self, monkeypatch, tmp_path):
        # 대각선이 아니므로(ja != en) 번역은 스킵되지 않는다 — 회귀 방지
        translator = self._translator(monkeypatch, tmp_path, include_pronunciation=False)
        called = []

        def fake_post(url, json, headers, timeout):
            called.append(json)
            from tests.test_nvidia_translator import chat_response

            return chat_response('[{"original":"きみの声が聴こえる","translation":"I hear your voice"}]')

        monkeypatch.setattr("everyric2.translation.translator.requests.post", fake_post)

        result = translator.translate(
            "きみの声が聴こえる", source_lang="ja", target_lang="en"
        )

        assert called
        assert result.translation_skipped is False
        assert result.lines[0].translation == "I hear your voice"


class TestDeterministicPronunciationMatrix:
    """발음 결정론 게이트(`_deterministic_pron_fn`)의 매트릭스 — 곡 원문 × 사용자 타깃
    5개 셀 각각 LLM이 돌려준 자유서술이 아니라 결정론 모듈(pron_style/ko_reading)의
    실제 출력과 일치하는지 확인한다. LLM 목·프롬프트 무발음 검증 패턴은
    test_pron_style.py의 translator 연동 절(_nvidia/_fake_post)과 같은 관례를 쓴다.

    매트릭스:
      ja×ko → wiki_pronunciation (기존, 무변경)
      ja×en → romaji_line(text)[0]
      ja×ja → 발음 자체를 스킵(대각선 — TestTranslationDiagonalEndToEnd에서 이미 검증)
      ko×ja → ko_reading.hangul_to_kana
      ko×en → ko_reading.hangul_to_romaja
      그 외(zh 등) → 결정론 렌더러 없음, 기존 LLM 자유서술 유지
    """

    def _nvidia(self, monkeypatch, tmp_path):
        key_file = tmp_path / "nvapi.txt"
        key_file.write_text("dummy-key", encoding="utf-8")
        monkeypatch.setattr(NvidiaTranslator, "_KEY_FILE", key_file)
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        settings = TranslationSettings(engine="nvidia", api_key=None)
        settings.include_pronunciation = True
        return NvidiaTranslator(settings)

    def _fake_post(self, monkeypatch, content: str) -> list[dict]:
        """requests.post를 고정 응답으로 대체하고 보낸 payload들을 돌려준다."""
        from tests.test_nvidia_translator import chat_response

        calls: list[dict] = []

        def post(url, json, headers, timeout):
            calls.append(json)
            return chat_response(content)

        monkeypatch.setattr("everyric2.translation.translator.requests.post", post)
        return calls

    def test_ja_to_ko_matches_wiki_pronunciation(self, monkeypatch, tmp_path):
        from everyric2.text.pron_style import wiki_pronunciation

        translator = self._nvidia(monkeypatch, tmp_path)
        calls = self._fake_post(
            monkeypatch,
            '[{"original":"君は王女","translation":"너는 왕녀",'
            '"pronunciation":"엉뚱한 LLM 값"}]',
        )

        result = translator.translate("君は王女", source_lang="ja", target_lang="ko")

        assert result.lines[0].pronunciation == wiki_pronunciation("君は王女")
        assert result.lines[0].pronunciation != "엉뚱한 LLM 값"
        # 결정론 경로를 타면 프롬프트에서 발음 요구를 뺀다 — 출력 토큰 절약
        assert "pronunciation" not in calls[0]["messages"][0]["content"]

    def test_ja_to_en_matches_romaji_line(self, monkeypatch, tmp_path):
        from everyric2.text.pron_style import romaji_line

        translator = self._nvidia(monkeypatch, tmp_path)
        calls = self._fake_post(
            monkeypatch,
            '[{"original":"君は王女","translation":"you are the princess",'
            '"pronunciation":"kimi wa oujo"}]',
        )

        result = translator.translate("君は王女", source_lang="ja", target_lang="en")

        expected = romaji_line("君は王女")[0]
        assert result.lines[0].pronunciation == expected
        assert result.lines[0].pronunciation != "kimi wa oujo"  # LLM 값은 버려진다
        assert "pronunciation" not in calls[0]["messages"][0]["content"]

    def test_ja_to_ja_skips_pronunciation_via_diagonal(self, monkeypatch, tmp_path):
        # ja×ja는 대각선(_should_skip_pronunciation)이 먼저 걸러 발음 자체를 묻지 않는다 —
        # _deterministic_pron_fn까지 오지 않는다는 것을 LLM 미호출로 확인한다.
        translator = self._nvidia(monkeypatch, tmp_path)

        def boom(*a, **k):  # pragma: no cover
            raise AssertionError("LLM must not be called for ja x ja")

        monkeypatch.setattr("everyric2.translation.translator.requests.post", boom)

        result = translator.translate(
            "君は王女\n夜の街に消えていく光", source_lang="ja", target_lang="ja"
        )

        assert result.translation_skipped is True
        assert all(line.pronunciation is None for line in result.lines)

    def test_ko_to_ja_matches_hangul_to_kana(self, monkeypatch, tmp_path):
        from everyric2.text.ko_reading import hangul_to_kana

        translator = self._nvidia(monkeypatch, tmp_path)
        calls = self._fake_post(
            monkeypatch,
            '[{"original":"사랑해","translation":"愛してる","pronunciation":"엉뚱한 값"}]',
        )

        result = translator.translate("사랑해", source_lang="ko", target_lang="ja")

        assert result.lines[0].pronunciation == hangul_to_kana("사랑해")
        assert result.lines[0].pronunciation != "엉뚱한 값"
        assert "pronunciation" not in calls[0]["messages"][0]["content"]

    def test_ko_to_en_matches_hangul_to_romaja(self, monkeypatch, tmp_path):
        from everyric2.text.ko_reading import hangul_to_romaja

        translator = self._nvidia(monkeypatch, tmp_path)
        calls = self._fake_post(
            monkeypatch,
            '[{"original":"사랑해","translation":"I love you","pronunciation":"엉뚱한 값"}]',
        )

        result = translator.translate("사랑해", source_lang="ko", target_lang="en")

        assert result.lines[0].pronunciation == hangul_to_romaja("사랑해")
        assert result.lines[0].pronunciation != "엉뚱한 값"
        assert "pronunciation" not in calls[0]["messages"][0]["content"]

    def test_non_matrix_source_keeps_llm_freeform_pronunciation(self, monkeypatch, tmp_path):
        # "그 외"(zh 등) — 결정론 렌더러가 없으므로 기존 LLM 자유서술 경로를 그대로 쓴다
        # (UniDic이 중국어를 오독하므로 ja 경로로 새지 않는지도 함께 확인)
        translator = self._nvidia(monkeypatch, tmp_path)
        calls = self._fake_post(
            monkeypatch,
            '[{"original":"我听到你的声音","translation":"네 목소리가 들려",'
            '"pronunciation":"워 팅 다오 니 더 성인"}]',
        )

        result = translator.translate("我听到你的声音", source_lang="zh", target_lang="ko")

        assert result.lines[0].pronunciation == "워 팅 다오 니 더 성인"
        assert "pronunciation" in calls[0]["messages"][0]["content"]

    def test_ko_to_en_pronunciation_has_no_fugashi_dependency(self, monkeypatch, tmp_path):
        # ko_reading은 자모 분해 규칙 기반이라 형태소 분석기 가용성과 무관하다 — ja 경로만
        # reading_source()=='fugashi'에 의존한다(기존 근거). pykakasi 폴백이어도 ko×en은
        # 여전히 결정론 경로를 탄다.
        from everyric2.text.ko_reading import hangul_to_romaja

        monkeypatch.setattr(
            "everyric2.translation.translator.reading_source", lambda: "pykakasi"
        )
        translator = self._nvidia(monkeypatch, tmp_path)
        calls = self._fake_post(
            monkeypatch,
            '[{"original":"사랑해","translation":"I love you","pronunciation":"엉뚱한 값"}]',
        )

        result = translator.translate("사랑해", source_lang="ko", target_lang="en")

        assert result.lines[0].pronunciation == hangul_to_romaja("사랑해")
        assert "pronunciation" not in calls[0]["messages"][0]["content"]

    def test_ja_to_en_falls_back_to_llm_when_no_morphological_analyzer(self, monkeypatch, tmp_path):
        # ja 경로만 reading_source()=='fugashi' 의존 — pykakasi 폴백이면 ja×en도 LLM으로 남는다
        monkeypatch.setattr(
            "everyric2.translation.translator.reading_source", lambda: "pykakasi"
        )
        translator = self._nvidia(monkeypatch, tmp_path)
        calls = self._fake_post(
            monkeypatch,
            '[{"original":"君は王女","translation":"you are the princess",'
            '"pronunciation":"kimi wa oujo"}]',
        )

        result = translator.translate("君は王女", source_lang="ja", target_lang="en")

        assert result.lines[0].pronunciation == "kimi wa oujo"
        assert "pronunciation" in calls[0]["messages"][0]["content"]
