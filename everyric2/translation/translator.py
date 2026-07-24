import json
import logging
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

from everyric2.config.settings import TranslationSettings, get_settings
from everyric2.inference.prompt import LyricLine

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class TranslationLine:
    original: str
    translation: str
    pronunciation: str | None = None
    # NIM 응답이 잘려(max_tokens 소진) 복구/재분할 후에도 이 라인만 살려내지 못한 경우 True.
    # 전체 500 대신 원문만 담아 반환하되, 어떤 라인이 실패했는지 결과에 남긴다.
    failed: bool = False


@dataclass
class TranslationResult:
    lines: list[TranslationLine]
    source_lang: str
    target_lang: str
    engine: str
    tone: str


_HANGUL_RE = re.compile(r"[가-힣]")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_OTHER_LETTER_RE = re.compile(r"[^\x00-\x7F가-힣\s\W]")
_JA_CHAR_RE = re.compile(r"[぀-ヿ㐀-鿿]")

_kakasi_reader = None


def _kana_readings(text: str) -> list[str] | None:
    """일본어 원문 각 라인의 히라가나 읽기 (pykakasi) — 발음 프롬프트의 정답 참조.

    일본어 문자가 없거나 pykakasi 사용 불가 시 None (힌트 없이 진행).
    라인 수·순서는 입력 텍스트의 줄과 1:1.
    """
    if not _JA_CHAR_RE.search(text):
        return None
    global _kakasi_reader
    try:
        if _kakasi_reader is None:
            import pykakasi

            _kakasi_reader = pykakasi.kakasi()
        readings = []
        for ln in text.split("\n"):
            ln = ln.strip()
            readings.append(
                "".join(item.get("hira", "") for item in _kakasi_reader.convert(ln))
                if ln
                else ""
            )
        return readings if any(readings) else None
    except Exception:
        logger.exception("kana reading hints failed; prompting without them")
        return None

# 발음 JSON 잘림(NIM max_tokens 소진) 복구 파라미터.
# - THRESHOLD 초과면 처음부터 배치로 나눠 요청(잘림 예방).
# - SIZE: 한 배치 라인 수. 8192 예산 안에서 30줄 발음 JSON은 안전(실측).
# - MAX_SPLIT_DEPTH: 잘림 복구 시 재귀 재분할 깊이 상한(요청 폭주 방지).
_PRON_BATCH_THRESHOLD = 60
_PRON_BATCH_SIZE = 30
_PRON_MAX_SPLIT_DEPTH = 4

TONE_PROMPTS = {
    "literal": "Translate literally, preserving the original meaning as closely as possible.",
    "natural": "Translate naturally so it sounds fluent to native speakers.",
    "poetic": "Translate poetically, maintaining rhythm, beauty, and artistic expression.",
    "casual": "Translate in casual, conversational language.",
    "formal": "Translate in formal, polite language.",
}


class BaseTranslator(ABC):
    def __init__(self, settings: TranslationSettings | None = None):
        self.settings = settings or get_settings().translation

    @abstractmethod
    def translate(
        self,
        lyrics: list[LyricLine] | str,
        source_lang: str = "auto",
        target_lang: str | None = None,
        context: str | None = None,
    ) -> TranslationResult:
        pass

    def _build_prompt(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        include_pronunciation: bool,
        context: str | None = None,
    ) -> str:
        lang_names = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}
        target = lang_names.get(target_lang, target_lang)
        tone_instruction = TONE_PROMPTS.get(self.settings.tone, TONE_PROMPTS["natural"])
        context_block = f"\nSong: {context}" if context else ""

        # 가사 맥락 지시 — 줄별 고립 직역(기계번역 톤)을 막고 곡 전체를 하나의 화자로 잇는다
        register_hint = (
            " Korean song lyrics use the plain intimate register (반말/해라체, e.g. ~해, ~야,"
            " ~잖아) — never formal endings like ~습니다/~어요 unless the original is"
            " explicitly formal."
            if target_lang == "ko"
            else ""
        )
        lyrics_guidance = (
            "These lines are the lyrics of ONE song, in order. Read the whole song first,"
            " then translate so the lines flow as a coherent song: keep one consistent"
            " speaker, emotional register and formality throughout, resolve omitted"
            " subjects/pronouns from surrounding lines, and prefer natural lyrical phrasing"
            f" over word-for-word rendering. Never translate a line in isolation.{register_hint}"
        )

        if include_pronunciation:
            # 일본어 원문이면 pykakasi 가나 읽기를 참조로 프롬프트에 심는다. 단 pykakasi는
            # 사전 읽기라 문맥 의존 한자를 오독한다 (실측: 君にだけ→くんにだけ — 노래에선
            # きみ). 그래서 '정답'이 아니라 '참조 + 오독은 문맥으로 교정'으로 지시한다.
            reading_block = ""
            readings = _kana_readings(text)
            if readings:
                reading_block = (
                    "\nREFERENCE READINGS (machine dictionary reading of each line, in order."
                    " The dictionary can misread context-dependent kanji — e.g. it may say"
                    " くん for 君 where the song sings きみ. Use these as a base and correct"
                    " such misreadings from the song's context):\n"
                    + "\n".join(f"{i + 1}. {r}" for i, r in enumerate(readings))
                    + "\n"
                )
            if target_lang == "ko":
                # 한글 독음은 서버가 가나에서 결정적으로 변환한다(kana_hangul) — LLM에겐
                # 문맥 판단이 필요한 '한자→가나'만 맡긴다. LLM의 가나→한글 기계 전사는
                # 촉음/ん 소실 실수가 잦았다 (ずっと→즈토, じぶんが→지부가 실측).
                pron_rule = (
                    "2. The full kana reading (ひらがな) of the ORIGINAL line — how the line"
                    " is actually sung. Convert every kanji to kana. FOLLOW the REFERENCE"
                    " READINGS below; deviate only where the dictionary clearly misread a"
                    " context-dependent kanji (e.g. 君 sung きみ, not くん) — okurigana words"
                    " like 消え=きえ are reliable as given. Write particles as pronounced"
                    " (は→わ, へ→え). Insert a space between sung phrases — a typical line"
                    " has 2-4 phrases (きみにだけ みえている) — but keep particles attached"
                    " to their word (きみにだけ, never きみ に だけ). Kana ONLY in this field."
                )
                pron_example = (
                    '[{"original": "時計の針が", "translation": "시곗바늘이",'
                    ' "pronunciation": "とけいの はりが"}]'
                )
                pron_note = (
                    "- pronunciation must be the kana reading of the ORIGINAL line"
                    " (hiragana, spaced by phrase) — never romanization, never a"
                    " translation, never Hangul"
                )
            else:
                pron_rule = "2. Romanized pronunciation of the ORIGINAL text (not the translation)"
                pron_example = '[{"original": "原文", "translation": "translation", "pronunciation": "genbun"}]'
                pron_note = "- pronunciation should be romanization of the ORIGINAL lyrics"
            return f"""Translate these song lyrics to {target}.
{tone_instruction}
{lyrics_guidance}{context_block}

For each line, provide:
1. The translation
{pron_rule}

Output as JSON array:
{pron_example}

IMPORTANT:
- Keep the same number of lines, in the same order
- Output ONLY the JSON array, no explanations
{pron_note}
{reading_block}
LYRICS:
{text}"""
        else:
            return f"""Translate these song lyrics to {target}.
{tone_instruction}
{lyrics_guidance}{context_block}

Keep the same line structure (same number of lines).
Only output the translation, no explanations or notes.

LYRICS:
{text}

TRANSLATION:"""

    def _parse_json_response(
        self, response: str, original_lines: list[str]
    ) -> list[TranslationLine]:
        response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
        response = response.strip()

        if response.startswith("```"):
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response, re.DOTALL)
            if match:
                response = match.group(1).strip()

        try:
            data = json.loads(response)
            if isinstance(data, list):
                results = []
                for i, item in enumerate(data):
                    if isinstance(item, dict):
                        orig = item.get(
                            "original", original_lines[i] if i < len(original_lines) else ""
                        )
                        results.append(
                            TranslationLine(
                                original=orig,
                                translation=item.get("translation", ""),
                                pronunciation=item.get("pronunciation"),
                            )
                        )
                return results
        except json.JSONDecodeError:
            pass

        array_match = re.search(r"\[.*\]", response, re.DOTALL)
        if array_match:
            try:
                data = json.loads(array_match.group())
                results = []
                for i, item in enumerate(data):
                    if isinstance(item, dict):
                        orig = item.get(
                            "original", original_lines[i] if i < len(original_lines) else ""
                        )
                        results.append(
                            TranslationLine(
                                original=orig,
                                translation=item.get("translation", ""),
                                pronunciation=item.get("pronunciation"),
                            )
                        )
                return results
            except json.JSONDecodeError:
                pass

        raise ValueError(f"Failed to parse JSON response: {response[:200]}")

    def _detect_lang_heuristic(self, text: str) -> str:
        """한글/ASCII 비율 기반 언어 추정. source_lang="auto"일 때 발음 생략 게이트에만 쓰이는
        거친 휴리스틱이며 실제 번역 언어 감지에는 관여하지 않는다."""
        hangul = len(_HANGUL_RE.findall(text))
        ascii_letters = len(_ASCII_LETTER_RE.findall(text))
        other_letters = len(_OTHER_LETTER_RE.findall(text))
        total = hangul + ascii_letters + other_letters
        if total == 0:
            return "en"
        if hangul / total >= 0.3:
            return "ko"
        if ascii_letters / total >= 0.5:
            return "en"
        return "other"

    def _should_skip_pronunciation(self, text: str, source_lang: str) -> bool:
        """원문이 영어/한국어면 로마자/한글 발음표기가 무의미하므로 생략한다.
        번역 자체는 그대로 수행되고 pronunciation 필드만 비운다."""
        lang = source_lang
        if lang == "auto":
            lang = self._detect_lang_heuristic(text)
        return lang in ("en", "ko")

    def _parse_text_response(
        self, response: str, original_lines: list[str]
    ) -> list[TranslationLine]:
        response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)

        for prefix in ["TRANSLATION:", "Translation:", "번역:", "Here is", "Here's"]:
            if response.strip().startswith(prefix):
                response = response.strip()[len(prefix) :].strip()

        lines = [line.strip() for line in response.strip().split("\n") if line.strip()]

        results = []
        for i, trans in enumerate(lines):
            orig = original_lines[i] if i < len(original_lines) else ""
            results.append(TranslationLine(original=orig, translation=trans, pronunciation=None))

        return results


class GeminiTranslator(BaseTranslator):
    def __init__(self, settings: TranslationSettings | None = None):
        super().__init__(settings)
        self.api_key = self.settings.api_key or os.getenv("GEMINI_API_KEY")
        self.model = self.settings.model
        self.api_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        )

    def translate(
        self,
        lyrics: list[LyricLine] | str,
        source_lang: str = "auto",
        target_lang: str | None = None,
        context: str | None = None,
    ) -> TranslationResult:
        target_lang = target_lang or self.settings.target_language

        if isinstance(lyrics, list):
            text = "\n".join(line.text for line in lyrics)
            original_lines = [line.text for line in lyrics]
        else:
            text = lyrics
            original_lines = [line.strip() for line in text.split("\n") if line.strip()]

        if not text.strip():
            return TranslationResult([], source_lang, target_lang, "gemini", self.settings.tone)

        if not self.api_key:
            return self._fallback_result(original_lines, source_lang, target_lang)

        include_pron = self.settings.include_pronunciation and not self._should_skip_pronunciation(
            text, source_lang
        )
        prompt = self._build_prompt(text, source_lang, target_lang, include_pron, context)

        try:
            response = requests.post(
                self.api_url,
                # 키는 URL 쿼리가 아니라 헤더로 — URL은 예외 메시지·로그에 그대로 찍힌다
                headers={"x-goog-api-key": self.api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": self.settings.temperature,
                        "maxOutputTokens": 8192,
                    },
                },
                timeout=self.settings.timeout,
            )

            if not response.ok:
                raise RuntimeError(f"API error: {response.status_code} - {response.text[:200]}")

            result = response.json()
            content = result["candidates"][0]["content"]["parts"][0]["text"]

            if include_pron:
                lines = self._parse_json_response(content, original_lines)
            else:
                lines = self._parse_text_response(content, original_lines)

            return TranslationResult(lines, source_lang, target_lang, "gemini", self.settings.tone)

        except requests.exceptions.ConnectionError:
            return self._fallback_result(original_lines, source_lang, target_lang)
        except Exception as e:
            raise RuntimeError(f"Translation failed: {e}") from e

    def _fallback_result(
        self, original_lines: list[str], source_lang: str, target_lang: str
    ) -> TranslationResult:
        # API 키가 없거나 연결이 안 되면 무료 웹 번역(deep-translator)으로 폴백.
        # 플레이스홀더 텍스트를 번역인 척 반환하면 클라이언트 UI에 그대로 노출되므로 금지 —
        # 여기서도 실패하면 예외를 올려 API가 5xx로 응답하게 한다(확장은 '번역 실패' 표시).
        from deep_translator import GoogleTranslator

        target = {"zh": "zh-CN"}.get(target_lang, target_lang)
        translator = GoogleTranslator(source="auto", target=target)

        translated = translator.translate("\n".join(original_lines)) or ""
        parts = [p.strip() for p in translated.split("\n")]
        if len(parts) != len(original_lines):
            # 웹 번역이 줄 수를 보존하지 못한 경우 — 줄 단위로 재시도 (느리지만 정확)
            parts = [(t or "").strip() for t in translator.translate_batch(original_lines)]

        lines = [
            TranslationLine(original=orig, translation=trans, pronunciation=None)
            for orig, trans in zip(original_lines, parts)
        ]
        return TranslationResult(lines, source_lang, target_lang, "google-web", self.settings.tone)


class OpenAICompatibleTranslator(BaseTranslator):
    def __init__(self, settings: TranslationSettings | None = None):
        super().__init__(settings)
        # 결과에 찍는 실제 백엔드 이름 — 자동 전환(gemini 설정→NIM) 시 settings.engine과 다르다
        self.engine_name = self.settings.engine
        self.api_key = self.settings.api_key or os.getenv("OPENAI_API_KEY") or "local-gen-ai"
        self.model = self.settings.model

        if self.settings.engine == "openai":
            self.api_url = "https://api.openai.com/v1/chat/completions"
        else:
            self.api_url = self.settings.api_url or "http://localhost:11434/v1/chat/completions"

    def translate(
        self,
        lyrics: list[LyricLine] | str,
        source_lang: str = "auto",
        target_lang: str | None = None,
        context: str | None = None,
    ) -> TranslationResult:
        target_lang = target_lang or self.settings.target_language

        if isinstance(lyrics, list):
            text = "\n".join(line.text for line in lyrics)
            original_lines = [line.text for line in lyrics]
        else:
            text = lyrics
            original_lines = [line.strip() for line in text.split("\n") if line.strip()]

        if not text.strip():
            return TranslationResult(
                [], source_lang, target_lang, self.engine_name, self.settings.tone
            )

        include_pron = self.settings.include_pronunciation and not self._should_skip_pronunciation(
            text, source_lang
        )

        try:
            if include_pron:
                # 발음 JSON은 라인 수가 많으면 응답이 max_tokens에서 잘려 파싱이 통째로
                # 실패한다(500). 프롬프트/요청/파싱/복구를 _translate_pron_lines에 위임해
                # 잘림을 감지·복구하고, 최악의 경우에도 원문만 담아 부분 성공으로 마감한다.
                lines = self._translate_pron_lines(
                    original_lines, source_lang, target_lang, context
                )
            else:
                prompt = self._build_prompt(
                    text, source_lang, target_lang, include_pron, context
                )
                content, _ = self._request_completion(prompt)
                lines = self._parse_text_response(content, original_lines)

            return TranslationResult(
                lines, source_lang, target_lang, self.engine_name, self.settings.tone
            )

        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"Connection failed to {self.api_url}: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Translation failed: {e}") from e

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _request_completion(
        self, prompt: str, *, allow_empty: bool = False
    ) -> tuple[str, str | None]:
        """단발 chat/completions 호출. (content, finish_reason)를 돌려준다.

        빈 응답(콘텐츠 필터/reasoning이 max_tokens 소진)은 1회 재시도한다. 그래도 비면
        allow_empty=False면 예외를, True면 ("", finish_reason)을 돌려줘 호출자가 잘림과
        동일하게 복구·재분할하도록 한다.
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        payload.update(self._payload_extras())

        content = ""
        finish_reason: str | None = None
        for attempt in range(2):
            response = requests.post(
                self.api_url,
                json=payload,
                headers=self._headers(),
                timeout=self.settings.timeout,
            )

            if not response.ok:
                raise RuntimeError(f"API error: {response.status_code} - {response.text[:200]}")

            result = response.json()
            choice = result["choices"][0]
            content = choice["message"].get("content") or ""
            finish_reason = choice.get("finish_reason")
            if content.strip():
                break
            logger.warning(
                "Empty completion content (attempt %d/2, finish_reason=%s); %s",
                attempt + 1,
                finish_reason,
                "retrying" if attempt == 0 else "giving up",
            )

        if not content.strip() and not allow_empty:
            raise RuntimeError(
                "Empty completion content (model may have spent max_tokens on reasoning)"
            )
        return content, finish_reason

    def _translate_pron_lines(
        self,
        original_lines: list[str],
        source_lang: str,
        target_lang: str,
        context: str | None,
    ) -> list[TranslationLine]:
        """발음 포함 번역. 긴 입력은 처음부터 배치로 나눠(잘림 예방) 각 배치를 복구
        로직으로 처리한 뒤 순서대로 이어붙인다."""
        if len(original_lines) > _PRON_BATCH_THRESHOLD:
            out: list[TranslationLine] = []
            for start in range(0, len(original_lines), _PRON_BATCH_SIZE):
                batch = original_lines[start : start + _PRON_BATCH_SIZE]
                out.extend(
                    self._translate_pron_batch(
                        batch, source_lang, target_lang, context, depth=0
                    )
                )
            return out
        return self._translate_pron_batch(
            original_lines, source_lang, target_lang, context, depth=0
        )

    def _translate_pron_batch(
        self,
        lines: list[str],
        source_lang: str,
        target_lang: str,
        context: str | None,
        depth: int,
    ) -> list[TranslationLine]:
        """한 배치를 요청하고, 응답이 잘렸으면(finish_reason=length 또는 완전 파싱 실패)
        복구한다: ① 잘린 JSON에서 완전한 객체까지 살려내고 ② 못 살린 나머지 라인만 재요청,
        진전이 없으면 ③ 라인을 절반으로 나눠 재귀. 깊이 한도를 넘거나 단일 라인도 실패하면
        ④ 원문만 담고 failed=True로 표시해 부분 성공으로 마감(전체 500 방지)."""
        text = "\n".join(lines)
        prompt = self._build_prompt(text, source_lang, target_lang, True, context)
        content, finish_reason = self._request_completion(prompt, allow_empty=True)

        parsed = self._salvage_json_lines(content, lines)
        # finish_reason=length여도 전 라인을 이미 완전 파싱했으면 잘림이 아니다(경계에서 정확히
        # 멈춘 경우) — 불필요한 빈 재요청을 막는다.
        truncated = len(parsed) < len(lines)
        if not truncated:
            return parsed[: len(lines)]

        logger.warning(
            "Pronunciation JSON incomplete (finish_reason=%s, content_len=%d, "
            "parsed %d/%d lines, depth=%d) — recovering",
            finish_reason,
            len(content),
            len(parsed),
            len(lines),
            depth,
        )

        if depth >= _PRON_MAX_SPLIT_DEPTH:
            # 더 못 쪼갠다 — 살려낸 앞부분은 유지하고 나머지는 원문만 반환
            return parsed + self._failed_lines(lines[len(parsed) :])

        covered = len(parsed)
        if covered > 0:
            # 완전한 앞부분은 확보 — 못 받은 나머지 라인만 다시 요청
            remainder = self._translate_pron_batch(
                lines[covered:], source_lang, target_lang, context, depth + 1
            )
            return parsed + remainder

        # 한 줄도 못 살렸다(빈/즉시 잘림) — 절반으로 쪼개 재귀, 단일 라인이면 폴백
        if len(lines) > 1:
            mid = len(lines) // 2
            left = self._translate_pron_batch(
                lines[:mid], source_lang, target_lang, context, depth + 1
            )
            right = self._translate_pron_batch(
                lines[mid:], source_lang, target_lang, context, depth + 1
            )
            return left + right

        return self._failed_lines(lines)

    @staticmethod
    def _failed_lines(lines: list[str]) -> list[TranslationLine]:
        """복구 불가 라인 — 원문만 담고 translation/pronunciation은 비운 채 failed 표시."""
        return [
            TranslationLine(original=o, translation="", pronunciation=None, failed=True)
            for o in lines
        ]

    def _salvage_json_lines(
        self, response: str, original_lines: list[str]
    ) -> list[TranslationLine]:
        """(잘렸을 수도 있는) JSON 배열에서 완전한 객체까지만 순서대로 복구한다.

        응답이 배열 중간에서 끊겨도 마지막 완전한 {..}까지 파싱한다. 예외를 던지지 않는다 —
        살릴 게 없으면 []를 돌려줘 호출자가 재요청/폴백하게 한다. len<=len(original_lines).
        """
        text = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        if text.startswith("```"):
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()

        start = text.find("[")
        if start == -1:
            return []

        results: list[TranslationLine] = []
        for i, item in enumerate(self._decode_json_objects(text, start + 1)):
            if not isinstance(item, dict):
                continue
            orig = item.get(
                "original", original_lines[i] if i < len(original_lines) else ""
            )
            results.append(
                TranslationLine(
                    original=orig,
                    translation=item.get("translation", ""),
                    pronunciation=item.get("pronunciation"),
                )
            )
        return results

    @staticmethod
    def _decode_json_objects(text: str, pos: int) -> list:
        """text[pos:]의 JSON 배열 원소를 raw_decode로 하나씩 읽어 완전한 값만 모은다.
        마지막 원소가 잘려 있으면 그 앞까지만 반환(truncation-safe)."""
        decoder = json.JSONDecoder()
        objs: list = []
        n = len(text)
        while pos < n:
            while pos < n and text[pos] in " \t\r\n,":
                pos += 1
            if pos >= n or text[pos] == "]":
                break
            try:
                obj, end = decoder.raw_decode(text, pos)
            except json.JSONDecodeError:
                break  # 마지막 원소가 잘림 — 여기서 중단
            objs.append(obj)
            pos = end
        return objs

    def _payload_extras(self) -> dict:
        """엔진별 추가 페이로드 훅 — 기본은 없음."""
        return {}


class NvidiaTranslator(OpenAICompatibleTranslator):
    """NVIDIA NIM (OpenAI 호환 /v1/chat/completions) 백엔드.

    키 해석 순서: settings.api_key -> env NVIDIA_API_KEY -> 루트 nvapi.txt 파일.
    모델은 gemini 기본값(settings.model)과 섞이지 않도록 settings.nvidia_model을 쓴다.
    """

    NIM_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
    _KEY_FILE = Path(__file__).resolve().parents[2] / "nvapi.txt"

    def __init__(self, settings: TranslationSettings | None = None):
        # OpenAICompatibleTranslator.__init__을 건너뛰고 BaseTranslator.__init__만 호출해
        # OPENAI_API_KEY/로컬 기본 URL 등 다른 엔진 전용 로직이 섞이지 않게 한다.
        BaseTranslator.__init__(self, settings)
        # settings.engine이 "gemini"여도(키 부재 자동 전환) 결과에는 실제 백엔드를 찍는다
        self.engine_name = "nvidia"
        self.api_key = (
            self.settings.api_key or os.getenv("NVIDIA_API_KEY") or self._read_key_file()
        )
        self.model = self.settings.nvidia_model
        self.api_url = self.settings.api_url or self.NIM_API_URL

    def _read_key_file(self) -> str | None:
        try:
            return self._KEY_FILE.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _payload_extras(self) -> dict:
        model = self.model.lower()
        # qwen3 계열은 reasoning 모델 — 사고 모드를 끄지 않으면 max_tokens를 사고에
        # 소진해 content가 비거나(빈 응답) 타임아웃이 난다. NIM qwen 챗 템플릿 스위치.
        if "qwen" in model:
            return {"chat_template_kwargs": {"thinking": False}}
        # gpt-oss도 reasoning 모델인데 thinking off 스위치가 없다 — effort를 최저로.
        # 기본 effort로는 30줄 가사에서 사고가 예산을 소진해 빈 응답/잘린 JSON이 났다.
        if "gpt-oss" in model:
            return {"reasoning_effort": "low"}
        return {}


class TranslatorFactory:
    @staticmethod
    def get_translator(settings: TranslationSettings | None = None) -> BaseTranslator:
        settings = settings or get_settings().translation

        if settings.engine == "gemini":
            # gemini 키가 없으면 웹 번역 폴백(번역만 가능, 발음표기 불가·기계번역 톤)으로
            # 조용히 격하된다 — NVIDIA 키(env 또는 루트 nvapi.txt)가 있으면 NIM으로 자동
            # 전환한다. env 없이 uvicorn만 띄운 서버에서 발음이 통째로 빠지는 사고 방지.
            if not (settings.api_key or os.getenv("GEMINI_API_KEY")):
                nvidia = NvidiaTranslator(settings)
                if nvidia.api_key:
                    logger.info(
                        "No Gemini API key; auto-switching translation engine to NVIDIA NIM"
                    )
                    return nvidia
            return GeminiTranslator(settings)
        elif settings.engine == "nvidia":
            return NvidiaTranslator(settings)
        elif settings.engine in ("openai", "local"):
            return OpenAICompatibleTranslator(settings)
        else:
            raise ValueError(f"Unknown translation engine: {settings.engine}")


class LyricsTranslator:
    def __init__(self, api_key: str | None = None, settings: TranslationSettings | None = None):
        if settings is None:
            settings = get_settings().translation
        if api_key:
            settings.api_key = api_key
        self._translator = TranslatorFactory.get_translator(settings)
        self.settings = settings

    def translate(
        self,
        lyrics: list[LyricLine] | str,
        source_lang: str = "auto",
        target_lang: str = "ko",
        context: str | None = None,
    ) -> str:
        result = self._translator.translate(lyrics, source_lang, target_lang, context)
        return "\n".join(line.translation for line in result.lines)

    def translate_with_pronunciation(
        self,
        lyrics: list[LyricLine] | str,
        source_lang: str = "auto",
        target_lang: str = "ko",
        context: str | None = None,
    ) -> TranslationResult:
        old_setting = self.settings.include_pronunciation
        self.settings.include_pronunciation = True
        try:
            result = self._translator.translate(lyrics, source_lang, target_lang, context)
        finally:
            self.settings.include_pronunciation = old_setting
        if target_lang == "ko":
            # LLM은 가나 독음까지만 책임진다 — 가나→한글은 결정적 변환으로 마감
            # (촉음=ㅅ받침, ん=ㄴ받침, 장음=모음 반복). 한글로 온 구형 응답은 그대로 둔다.
            from everyric2.text.kana_hangul import finalize_pronunciation

            for line in result.lines:
                line.pronunciation = finalize_pronunciation(line.pronunciation)
        return result
