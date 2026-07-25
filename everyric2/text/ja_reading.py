"""일본어 텍스트 → 토큰별 (원문 표면, 히라가나 읽기, 원문 오프셋).

"일본어를 어떻게 읽는가"의 단일 소유 모듈이다. 읽기는 표시용 발음만의 문제가 아니다 —
``text.reading``이 이 읽기로 모라를 쪼개고, 그 모라 열이 발음 음절 타이밍 DP 정렬의
입력이다. 독음이 틀리면 모라 열이 틀어져 가라오케 음절 타이밍이 그대로 어긋난다.

그래서 사전 표제어를 문맥 없이 긁는 pykakasi 대신 형태소 분석(fugashi + unidic-lite)을
1순위로 쓴다. 실측: 今更止められない → pykakasi는 いまさら"やめ"られない (정답 とめ),
縋って → "つい"って (정답 すがって). 형태소 분석은 둘 다 맞히고 涙を止める(とめる) /
風が止む(やむ)의 문맥 구분까지 한다. fugashi/unidic-lite를 못 쓰는 환경에서는 pykakasi로
폴백하며(어느 쪽을 썼는지는 ``reading_source``로 노출), 어느 경로든 계약은 같다.

계약: **토큰 표면을 순서대로 이어 붙이면 원문이 정확히 복원된다** (공백·기호·라틴·숫자
포함). ``reading.py``의 모라 글자 오프셋과 ``map_pron_alignment_to_line``의 원문 역매핑,
worker의 ``_full_coverage_words``가 모두 이 불변식에 걸려 있다.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_KANJI_RE = re.compile(r"[㐀-鿿]")

# UniDic의 kana/pron은 가타카나다 — 모라 표(reading.py)와 한글 변환표(kana_hangul)는
# 히라가나 기준이라 여기서 내려준다. 장음부 ー(0x30FC)는 ァ~ヶ 범위 밖이라 그대로 남고
# 촉음 ッ은 っ로 내려간다 — 둘 다 1박을 차지하므로 읽기에 남겨야 모라 수가 맞는다.
_KATAKANA_START, _KATAKANA_END = "ァ", "ヶ"


@dataclass
class ReadingToken:
    """형태소 토큰 1개: 원문 표면 + 히라가나 읽기 + 원문 글자 구간 [start, end)."""

    surface: str
    reading: str
    start: int
    end: int


# ---------------------------------------------------------------------------
# 엔진 (지연 생성 싱글턴)
# ---------------------------------------------------------------------------

_tagger = None
_tagger_unavailable = False
_kakasi = None
# fugashi(MeCab) Tagger도 pykakasi 변환기도 내부 상태를 들고 있어 스레드 안전하지 않다.
# 번역 배치와 정렬 워커가 여러 스레드에서 동시에 부르므로(translator.py가 _kakasi_lock으로
# 막던 것과 같은 사정) 지연 생성 경합과 변환 구간을 한 락으로 함께 감싼다 — 라인당
# 밀리초 수준이라 8초짜리 HTTP 호출 옆에서는 직렬화 비용이 사실상 없다.
# 폴백 사다리가 락 안에서 다시 엔진을 잡으므로 재진입 가능한 RLock이어야 한다.
_lock = threading.RLock()


def _create_tagger():
    """fugashi Tagger 생성. 임포트 실패도 여기서 터지게 모아 둔다(폴백 판정 지점)."""
    import fugashi

    return fugashi.Tagger()


def _get_tagger():
    """형태소 분석기 지연 생성 싱글턴. 사용 불가 환경이면 None (pykakasi 폴백)."""
    global _tagger, _tagger_unavailable
    with _lock:
        if _tagger is None and not _tagger_unavailable:
            try:
                _tagger = _create_tagger()
            except Exception:
                # 의존성 미설치·사전 로드 실패 — 라인마다 재시도하지 않게 못 박는다
                _tagger_unavailable = True
                logger.warning(
                    "fugashi/unidic-lite unavailable; falling back to pykakasi readings",
                    exc_info=True,
                )
        return _tagger


def _get_kakasi():
    global _kakasi
    with _lock:
        if _kakasi is None:
            import pykakasi

            _kakasi = pykakasi.kakasi()
        return _kakasi


def _katakana_to_hiragana(text: str) -> str:
    return "".join(
        chr(ord(ch) - 0x60) if _KATAKANA_START <= ch <= _KATAKANA_END else ch for ch in text
    )


def _pykakasi_reading(surface: str) -> str:
    """표면 하나를 pykakasi로 읽는다. 실패 시 표면 그대로."""
    try:
        return "".join(item.get("hira", "") for item in _get_kakasi().convert(surface)) or surface
    except Exception:
        logger.warning("pykakasi reading failed for %r", surface, exc_info=True)
        return surface


# ---------------------------------------------------------------------------
# 토큰화
# ---------------------------------------------------------------------------


def _token_reading(word) -> str:
    """토큰 1개의 히라가나 읽기. 폴백 사다리(위에서부터):

    1. ``feature.kana`` — UniDic의 표층 읽기(활용형 그대로). 1순위.
    2. ``feature.pron`` — 발음형. kana가 비고 pron만 채워진 항목이 있다. 순서가 중요한데,
       조사 は는 kana=ハ / pron=ワ라 pron을 먼저 보면 모라가 は가 아니라 わ가 되어
       ``reading.py``의 가나 표·DP 비용이 기대하는 표층 읽기와 어긋난다.
    3. 표면에 한자가 있으면 **그 토큰만** pykakasi로 읽는다 — UniDic 미등록 한자어
       (가사에 흔한 조어·이체자)를 표면 그대로 흘리면 모라가 통째로 비어 타이밍이 무너진다.
    4. 그 외(라틴·숫자·기호)는 표면 그대로 — reading.py가 ASCII 유닛으로 따로 센다.
    """
    feature = word.feature
    for attr in ("kana", "pron"):
        value = getattr(feature, attr, None)
        if value and value != "*":
            return _katakana_to_hiragana(value)
    if _KANJI_RE.search(word.surface):
        return _pykakasi_reading(word.surface)
    return word.surface


def _tokenize_with_tagger(tagger, text: str) -> list[ReadingToken]:
    """형태소 토큰을 원문 오프셋에 다시 앉힌다.

    MeCab은 공백을 토큰으로 내주지 않는다 — 표면을 그냥 이어 붙이면 원문보다 짧아져
    이후 오프셋이 전부 밀리고, 그 밀림은 예외 없이 조용히 발음 타이밍을 망가뜨린다.
    그래서 표면을 원문에서 앞으로 검색해 위치를 다시 잡고, 건너뛴 구간(공백 등)은
    읽기=표면인 리터럴 토큰으로 내보내 '표면 이어 붙이기 = 원문' 계약을 지킨다.
    """
    tokens: list[ReadingToken] = []
    pos = 0
    for word in tagger(text):
        surface = word.surface
        if not surface:
            continue
        idx = text.find(surface, pos)
        if idx < 0:
            # 표면이 원문에서 안 잡히는 이례적 경우(정규화 등) — 읽기 하나를 잃더라도
            # 오프셋 계약은 지킨다. 아래 리터럴 보충이 이 구간을 원문 그대로 덮는다.
            continue
        if idx > pos:
            tokens.append(ReadingToken(text[pos:idx], text[pos:idx], pos, idx))
        tokens.append(ReadingToken(surface, _token_reading(word), idx, idx + len(surface)))
        pos = idx + len(surface)
    if pos < len(text):
        tokens.append(ReadingToken(text[pos:], text[pos:], pos, len(text)))
    return tokens


def _tokenize_with_kakasi(text: str) -> list[ReadingToken]:
    """pykakasi 폴백. convert의 'orig'는 이어 붙이면 원문이 복원되므로 누적 오프셋으로
    구간을 매긴다 (형태소 경로와 계약 동일)."""
    try:
        items = _get_kakasi().convert(text)
    except Exception:
        logger.warning("pykakasi unavailable; emitting the line as one literal token", exc_info=True)
        return [ReadingToken(text, text, 0, len(text))]

    tokens: list[ReadingToken] = []
    pos = 0
    for item in items:
        surface = item.get("orig", "")
        if not surface:
            continue
        end = pos + len(surface)
        tokens.append(ReadingToken(surface, item.get("hira") or surface, pos, end))
        pos = end
    if pos < len(text):
        tokens.append(ReadingToken(text[pos:], text[pos:], pos, len(text)))
    return tokens


def tokenize_reading(text: str) -> list[ReadingToken]:
    """일본어 텍스트를 (표면, 히라가나 읽기, 원문 오프셋) 토큰 열로 쪼갠다.

    표면을 순서대로 이어 붙이면 항상 원문이 복원되고, 각 토큰은
    ``text[token.start:token.end] == token.surface``를 만족한다.
    """
    if not text:
        return []
    with _lock:
        tagger = _get_tagger()
        if tagger is None:
            return _tokenize_with_kakasi(text)
        try:
            return _tokenize_with_tagger(tagger, text)
        except Exception:
            logger.warning(
                "morphological tokenization failed; falling back to pykakasi", exc_info=True
            )
            return _tokenize_with_kakasi(text)


def kana_reading(text: str) -> str:
    """텍스트 전체의 히라가나 읽기 (비일본어 구간은 원문 그대로 통과)."""
    return "".join(token.reading for token in tokenize_reading(text))


def reading_source() -> str:
    """실제로 쓰이는 읽기 엔진 이름: ``"fugashi"`` 또는 ``"pykakasi"``.

    호출부가 엔진에 따라 프롬프트를 달리 짜야 하기 때문에 노출한다 (translator의
    다의어 후보 주석은 사전 읽기 폴백일 때만 의미가 있다).
    """
    return "fugashi" if _get_tagger() is not None else "pykakasi"
