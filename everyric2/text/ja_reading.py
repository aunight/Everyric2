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
import unicodedata
from dataclasses import dataclass

from everyric2.text.ja_numbers import digits_to_reading

logger = logging.getLogger(__name__)

_KANJI_RE = re.compile(r"[㐀-鿿]")

# UniDic의 kana/pron은 가타카나다 — 모라 표(reading.py)와 한글 변환표(kana_hangul)는
# 히라가나 기준이라 여기서 내려준다. 장음부 ー(0x30FC)는 ァ~ヶ 범위 밖이라 그대로 남고
# 촉음 ッ은 っ로 내려간다 — 둘 다 1박을 차지하므로 읽기에 남겨야 모라 수가 맞는다.
_KATAKANA_START, _KATAKANA_END = "ァ", "ヶ"

# 표층에서 읽기를 직접 뽑을 때 제외하는 품사. 조사·조동사는 표기와 음가가 갈리는 유일한
# 부류다(は→ワ, へ→エ) — 가타카나로 적힌 조사(ハ)까지 표층대로 읽으면 그 대립이 사라진다.
_PARTICLE_POS = frozenset({"助詞", "助動詞"})

# 가타카나 표층에서 읽기를 뽑을 때 통과시키는 글자. 가나·장음부·촉음만 허용하고
# 라틴·숫자·기호가 섞이면 규칙을 쓰지 않는다(그건 사전/폴백이 다룰 몫이다).
_KANA_ONLY_RE = re.compile(r"^[ぁ-ゖァ-ヺー゛゜ｦ-ﾟ]+$")
_HAS_KATAKANA_RE = re.compile(r"[ァ-ヺｦ-ﾟ]")

# 접두사 뒤에 이것이 오면 붙을 내용어가 없다 — 공백·문장부호·줄끝 (_is_orphan_prefix 참조)
_ORPHAN_TAIL_RE = re.compile(r"^[\s、。，．,\.！？!?…‥・「」『』（）\(\)\[\]〜~ー\-—/／]")

# UniDic이 数詞로 묶는 아라비아 숫자 토큰의 표면 형태 (_numeral_override 참조)
_ARABIC_DIGITS_RE = re.compile(r"^[0-9]+$")

# 아라비아 숫자 뒤에서 자릿수 읽기를 적용하는 조수사 — 실측(보카로 위키 사람 발음 표본
# tests/fixtures/wiki_pron_sample.json 115줄)에서 아라비아 숫자 뒤에 조수사가 온 사례는
# "1秒"(→ 이치뵤오) 단 1건이었다. 秒는 앞자리 숫자와 무관하게 촉음화·반탁음화가 없는
# 규칙 조수사라(いちびょう/にびょう/さんびょう/…) 자릿수 읽기를 그대로 이어 붙이면 된다.
# 分・回・人처럼 음변화가 있는 조수사는 이 표본에 없어 넣지 않는다 — 잘못된 음변화를
# 새로 만드는 것이 기존의 "숫자 그대로" 동작보다 나쁘다(_numeral_override 참조).
_MEASURED_ARABIC_COUNTERS = frozenset({"秒"})


def _katakana_as_hiragana(surface: str) -> str | None:
    """가타카나 표층을 히라가나 읽기로 바꾼다. 규칙을 쓸 수 없으면 ``None``.

    가타카나는 표음 문자라 표층이 곧 읽기다. 사전 조회는 이 경우 이득이 없고 손해만 있다 —
    실측 오독 2종(エグい→えぎい, レイニー→れーにー)이 그 예다. 반각 가나도 함께 받아
    전각으로 정규화한다(``ｱｲｳ``류가 옛 자막에 남아 있다).

    한자가 섞이면 쓰지 않는다 — 그건 사전이 필요한 진짜 조회 대상이다.
    """
    if not surface or _KANJI_RE.search(surface):
        return None
    norm = unicodedata.normalize("NFKC", surface)
    if not _HAS_KATAKANA_RE.search(norm) or not _KANA_ONLY_RE.match(norm):
        return None
    out: list[str] = []
    for ch in norm:
        # ァ~ヶ만 내린다. ー(장음)·ヷ~ヺ는 대응 히라가나가 없거나 범위 밖이라 그대로 둔다.
        out.append(
            chr(ord(ch) - 0x60) if _KATAKANA_START <= ch <= _KATAKANA_END else ch
        )
    return "".join(out)


@dataclass
class ReadingToken:
    """형태소 토큰 1개: 원문 표면 + 히라가나 읽기 + 원문 글자 구간 [start, end).

    ``pos``/``pos2``는 UniDic ``feature.pos1``/``pos2``(名詞-普通名詞, 動詞-非自立可能…).
    위키식 발음 표기의 문절(文節) 띄어쓰기가 품사 경계를 봐야 해서 실어 보낸다
    (``text.pron_style``). pos2까지 필요한 이유는 補助動詞다 — ``〜ている``/``〜てしまう``의
    いる·しまう는 pos1이 動詞라 그대로 두면 문절이 갈라지는데(``맛테 이루``), 위키는
    앞 문절에 붙여 쓴다(``맛테이루``). 그 판정이 pos2=非自立可能에만 걸려 있다.
    폴백(pykakasi) 경로와 공백 등 리터럴 토큰은 품사가 없어 빈 문자열이다.

    ``surface_reading``은 같은 토큰의 **표층 읽기**(``feature.kana``)다. ``phonetic=True``로
    받은 ``reading``(음가)과 나란히 두고 비교하는 용도로만 있다 — 음가는 エイ를 장음 ー로
    뭉개므로(鮮明 kana=センメイ / pron=センメー) 두 읽기를 맞춰 보면 "여기가 원래 い였다"를
    알 수 있다(``pron_style._restore_ei``). 표층 읽기를 따로 알 수 없는 토큰(리터럴·폴백)은
    빈 문자열이며, 그 경우 비교하는 쪽이 아무것도 하지 않는다.
    """

    surface: str
    reading: str
    start: int
    end: int
    pos: str = ""
    pos2: str = ""
    surface_reading: str = ""


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


def _feature_reading(feature, attrs: tuple[str, ...]) -> str | None:
    """``attrs`` 순서로 UniDic 읽기 필드를 훑어 첫 유효값을 히라가나로 내린다."""
    for attr in attrs:
        value = getattr(feature, attr, None)
        if value and value != "*":
            return _katakana_to_hiragana(value)
    return None


def _is_orphan_prefix(words, i: int, text: str, idx: int) -> bool:
    """이 토큰이 **붙을 말이 없는 접두사**인가.

    접두사 읽기는 뒤에 오는 내용어와 한 낱말을 이룰 때만 성립한다. UniDic은 홀로 선 한자를
    접두사로 잡을 때가 있고, 그러면 접두사 전용 읽기가 나와 틀린다 — 실측(위키 사람 발음):
    「さり気ない愛 盛りすぎる愛」의 첫 愛가 接頭辞/まな로 읽혀 「마나」가 됐다(정답 「아이」).
    같은 줄의 두 번째 愛는 名詞/あい로 제대로 읽혔다. 즉 사전이 아니라 **자리**가 문제다.

    "붙을 말이 없다"의 판정: 다음 토큰이 없거나, 원문에서 이 토큰 바로 뒤가 공백·문장부호다
    (그 경우 접두사가 될 수 없다). 참이면 접두사 읽기를 버리고 폴백 사다리로 내려보낸다.
    """
    word = words[i]
    if (getattr(word.feature, "pos1", "") or "") != "接頭辞":
        return False
    tail = text[idx + len(word.surface) :]
    if not tail:
        return True
    return bool(_ORPHAN_TAIL_RE.match(tail))


def _numeral_override(words, i: int, text: str, idx: int) -> str | None:
    """아라비아 숫자 토큰 ``words[i]``를 자릿수 읽기로 바꿀지 판정.

    UniDic은 아라비아 숫자에 읽기를 주지 않아(``feature.kana``/``pron`` 둘 다 빈값) 사다리가
    표면 그대로("1")로 떨어진다(``_token_readings`` 3·4단 참조) — 한자 숫자(一/二/三…)는
    사전에 읽기가 있어 이 문제가 없다(실측: ``一分``→いち+ふん처럼 숫자 자리는 이미
    맞다). 여기서 그 빈 자리만 메운다.

    바로 뒤 토큰이 ``_MEASURED_ARABIC_COUNTERS``에 있고 원문에서 숫자와 공백·기호 없이
    바로 붙어 있을 때만 적용한다 — 실측 밖 조합(예: "1 秒"처럼 사이가 뜬 경우, 또는 分・回
    같은 음변화 조수사)은 건드리지 않고 기존 동작(숫자 그대로)을 유지한다.
    """
    surface = words[i].surface
    if not _ARABIC_DIGITS_RE.match(surface):
        return None
    feature = words[i].feature
    if (getattr(feature, "pos1", "") or "") != "名詞" or (getattr(feature, "pos2", "") or "") != "数詞":
        return None
    if i + 1 >= len(words):
        return None
    nxt = words[i + 1].surface
    if nxt not in _MEASURED_ARABIC_COUNTERS:
        return None
    tail_start = idx + len(surface)
    if text[tail_start : tail_start + len(nxt)] != nxt:
        return None
    return digits_to_reading(surface)


def _token_readings(
    word, *, phonetic: bool = False, orphan_prefix: bool = False
) -> tuple[str, str]:
    """토큰 1개의 (읽기, 표층 읽기). 폴백 사다리(위에서부터):

    1. ``feature.kana`` — UniDic의 표층 읽기(활용형 그대로). 1순위.
    2. ``feature.pron`` — 발음형. kana가 비고 pron만 채워진 항목이 있다. 순서가 중요한데,
       조사 は는 kana=ハ / pron=ワ라 pron을 먼저 보면 모라가 は가 아니라 わ가 되어
       ``reading.py``의 가나 표·DP 비용이 기대하는 표층 읽기와 어긋난다.
    3. 표면에 한자가 있으면 **그 토큰만** pykakasi로 읽는다 — UniDic 미등록 한자어
       (가사에 흔한 조어·이체자)를 표면 그대로 흘리면 모라가 통째로 비어 타이밍이 무너진다.
    4. 그 외(라틴·숫자·기호)는 표면 그대로 — reading.py가 ASCII 유닛으로 따로 센다.

    위 "kana를 먼저 보는 이유"는 **모라 정렬용 기본값**에만 해당한다. ``phonetic=True``면
    1·2의 순서를 뒤집어 ``pron``(음가)을 먼저 본다 — 사람이 쓴 발음 표기는 표층이 아니라
    음가를 적기 때문이다(조사 は→ワ, 王女→オージョ). 실측(보카로 위키 사람 발음 2,207줄
    완전일치): kana 우선 72.0% vs pron 우선 75.5%. 반대로 모라 열은 표층 읽기를 기대하므로
    ``text_to_moras``는 기본값(kana 우선)을 계속 쓴다.

    두 번째 반환값은 항상 **표층 읽기(kana 우선)** 다 — 음가가 뭉갠 자리를 되살리려는
    호출부가 두 읽기를 나란히 비교할 수 있게 함께 내려보낸다(``ReadingToken`` 참조).
    """
    feature = word.feature
    # 0단: 가타카나 표기는 **이미 표음 문자**라 사전을 조회할 이유가 없다. 조회하면 오히려
    # 틀린다 — 실측(위키 사람 발음): エグい를 UniDic이 えぎい로 읽고(에기이요, 정답 에구이요),
    # レイニー의 pron이 れーにー로 장음을 뭉개 레에니이가 된다(정답 레이니이). 두 사례가
    # 독음오류 48줄 중 9줄이었다. 표층을 그대로 가나로 바꾸면 둘 다 사라진다.
    surface_kana = _katakana_as_hiragana(word.surface)
    if surface_kana is not None and (getattr(feature, "pos1", "") or "") not in _PARTICLE_POS:
        return surface_kana, surface_kana
    order = ("pron", "kana") if phonetic else ("kana", "pron")
    reading = None if orphan_prefix else _feature_reading(feature, order)
    if reading is None:
        # 사다리 3·4단: UniDic에 읽기가 없는 토큰 — 이 경우 표층/음가 구분 자체가 없다
        fallback = (
            _pykakasi_reading(word.surface)
            if _KANJI_RE.search(word.surface)
            else word.surface
        )
        return fallback, fallback
    surface_reading = None if orphan_prefix else _feature_reading(feature, ("kana", "pron"))
    return reading, surface_reading or reading


def _tokens_from_words(words, text: str, *, phonetic: bool = False) -> list[ReadingToken]:
    """형태소 토큰을 원문 오프셋에 다시 앉힌다.

    MeCab은 공백을 토큰으로 내주지 않는다 — 표면을 그냥 이어 붙이면 원문보다 짧아져
    이후 오프셋이 전부 밀리고, 그 밀림은 예외 없이 조용히 발음 타이밍을 망가뜨린다.
    그래서 표면을 원문에서 앞으로 검색해 위치를 다시 잡고, 건너뛴 구간(공백 등)은
    읽기=표면인 리터럴 토큰으로 내보내 '표면 이어 붙이기 = 원문' 계약을 지킨다.

    ``words``는 1순위 파스(``tagger(text)``)든 N-best 대안 파스(``nbestToNodeList``)든
    같은 노드 열이라 같은 규칙으로 앉힌다 — 대안 파스마다 규칙을 복제하면 오프셋 계약이
    한쪽에서만 깨진다.
    """
    tokens: list[ReadingToken] = []
    pos = 0
    for i, word in enumerate(words):
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
        reading, surface_reading = _token_readings(
            word, phonetic=phonetic, orphan_prefix=_is_orphan_prefix(words, i, text, idx)
        )
        numeral = _numeral_override(words, i, text, idx)
        if numeral is not None:
            reading = surface_reading = numeral
        tokens.append(
            ReadingToken(
                surface,
                reading,
                idx,
                idx + len(surface),
                pos=getattr(word.feature, "pos1", "") or "",
                pos2=getattr(word.feature, "pos2", "") or "",
                surface_reading=surface_reading,
            )
        )
        pos = idx + len(surface)
    if pos < len(text):
        tokens.append(ReadingToken(text[pos:], text[pos:], pos, len(text)))
    return tokens


def _tokenize_with_tagger(tagger, text: str, *, phonetic: bool = False) -> list[ReadingToken]:
    return _tokens_from_words(tagger(text), text, phonetic=phonetic)


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


# 루비(후리가나) 표기: 한자 뭉치 바로 뒤에 괄호로 읽기를 적어 둔 가사 관례.
# 실측 예: 涙（シル）, 動画（トコ）, 時間（トキ）, 誕生日(バースデー) — 반자 괄호도 쓰인다.
_RUBY_RE = re.compile(r"[㐀-鿿々]+[（(]([぀-ヿ]+)[）)]")


def _adopt_ruby_readings(text: str, tokens: list[ReadingToken]) -> None:
    """``한자런（가나）`` 패턴에서 괄호 안 가나만 읽고 한자·괄호의 읽기는 비운다(제자리 수정).

    가사가 루비를 달아 둔 곳은 작사가가 지정한 독음이므로 사전 독음보다 정확하다
    (涙（シル）는 "나미다"가 아니라 "시루"로 불린다). 실측 순이득 +0.7p(개선 14줄,
    악화 6줄) — 위키가 한자 독음과 루비를 **둘 다** 적는 경우가 있어 손실이 섞인다.
    그래서 기본값이 아니라 옵션으로 둔다.

    괄호 자체는 읽기에서 사라지지만(읽기는 가나 열이다) 표면·오프셋 계약은 건드리지
    않으므로, 괄호를 표기에 남기고 싶은 호출부는 ``token.surface``를 보면 된다
    (``pron_style``이 그렇게 한다 — 위키도 ``(토코)``처럼 괄호를 남긴다).
    """
    for match in _RUBY_RE.finditer(text):
        kana_start, kana_end = match.start(1), match.end(1)
        for token in tokens:
            if token.end <= match.start() or token.start >= match.end():
                continue
            if kana_start <= token.start and token.end <= kana_end:
                continue  # 괄호 안 가나 — 읽기 유지
            if token.start <= kana_start and kana_end <= token.end:
                # 형태소 분석기가 루비 전체를 한 토큰으로 삼킨 경우 — 가나만 남긴다
                token.reading = _katakana_to_hiragana(text[kana_start:kana_end])
            else:
                token.reading = ""


def tokenize_reading(
    text: str, *, phonetic: bool = False, adopt_ruby: bool = False
) -> list[ReadingToken]:
    """일본어 텍스트를 (표면, 히라가나 읽기, 원문 오프셋) 토큰 열로 쪼갠다.

    표면을 순서대로 이어 붙이면 항상 원문이 복원되고, 각 토큰은
    ``text[token.start:token.end] == token.surface``를 만족한다. 두 옵션은 **읽기만**
    바꾸며 이 계약에는 손대지 않는다.

    Args:
        phonetic: 읽기 사다리를 ``feature.pron``(음가) 우선으로 바꾼다 — 사람이 쓴 발음
            표기를 재현할 때 쓴다(``_token_readings`` 참조). 기본값은 표층 읽기(kana)
            우선이며 모라 정렬(``reading.py``)이 그 값을 전제한다.
        adopt_ruby: ``한자런（가나）``의 괄호 안 가나만 읽는다(``_adopt_ruby_readings``).
    """
    if not text:
        return []
    with _lock:
        tagger = _get_tagger()
        if tagger is None:
            tokens = _tokenize_with_kakasi(text)
        else:
            try:
                tokens = _tokenize_with_tagger(tagger, text, phonetic=phonetic)
            except Exception:
                logger.warning(
                    "morphological tokenization failed; falling back to pykakasi", exc_info=True
                )
                tokens = _tokenize_with_kakasi(text)
    if adopt_ruby:
        _adopt_ruby_readings(text, tokens)
    return tokens


def has_ruby(text: str) -> bool:
    """``한자런（가나）`` 루비가 있는 라인인가 (``adopt_ruby``가 실제로 뭔가 바꿀 라인인가)."""
    return bool(text) and _RUBY_RE.search(text) is not None


def tokenize_reading_nbest(
    text: str, *, n: int = 8, phonetic: bool = False, adopt_ruby: bool = False
) -> list[list[ReadingToken]]:
    """같은 라인의 **대안 파스**(MeCab N-best) 토큰 열들. 1순위 파스는 제외한다.

    남은 오독은 대부분 "어느 독음이 맞나" 하나로 수렴하고(사람 발음 2,207줄 대비 82.1%),
    그 갈림이 정확히 MeCab의 후순위 파스에 들어 있다 — ``私は三日月を見た``의 nbest는
    ワタクシ/ワタシ와 ミッカツキ/ミッカズキ/ミカツキ를 모두 내놓는다(실측). 사전은 어느
    쪽이 맞는지 모르지만 오디오는 알기 때문에(``ctc_engine`` 심판) 후보만 만들어 준다.

    실패(폴백 엔진·미설치·nbest 예외)는 빈 목록이다 — 후보가 없으면 심판이 안 돌고
    기존 동작 그대로다. 오프셋·표면 계약은 1순위 파스와 동일하다(``_tokens_from_words``).
    """
    if not text or n <= 1:
        return []
    parses: list[list[ReadingToken]] = []
    with _lock:
        tagger = _get_tagger()
        if tagger is None:
            return []
        try:
            # 1순위 파스는 호출부가 이미 갖고 있으므로 건너뛴다 (첫 열 = tagger(text))
            for i, nodes in enumerate(tagger.nbestToNodeList(text, n)):
                if i == 0:
                    continue
                parses.append(_tokens_from_words(nodes, text, phonetic=phonetic))
        except Exception:
            logger.warning("MeCab n-best parse failed for %r", text, exc_info=True)
            return []
    if adopt_ruby:
        for tokens in parses:
            _adopt_ruby_readings(text, tokens)
    return parses


def tokenize_reading_pykakasi(text: str) -> list[ReadingToken]:
    """사전 표제어 기반(pykakasi) 토큰 열 — 형태소 분석과 **한자 독음이 갈리는** 변종.

    폴백 경로를 후보 생성에 재사용한다: 今更止められない를 pykakasi는 いまさら"やめ"られない로
    읽고 형태소 분석은 とめ로 읽는다(실측). 어느 쪽이 맞는지는 오디오가 고른다.
    """
    if not text:
        return []
    with _lock:
        return _tokenize_with_kakasi(text)


def kana_reading(text: str, *, phonetic: bool = False, adopt_ruby: bool = False) -> str:
    """텍스트 전체의 히라가나 읽기 (비일본어 구간은 원문 그대로 통과).

    옵션 의미는 ``tokenize_reading``과 같다.
    """
    return "".join(
        token.reading
        for token in tokenize_reading(text, phonetic=phonetic, adopt_ruby=adopt_ruby)
    )


def reading_source() -> str:
    """실제로 쓰이는 읽기 엔진 이름: ``"fugashi"`` 또는 ``"pykakasi"``.

    호출부가 엔진에 따라 프롬프트를 달리 짜야 하기 때문에 노출한다 (translator의
    다의어 후보 주석은 사전 읽기 폴백일 때만 의미가 있다).
    """
    return "fugashi" if _get_tagger() is not None else "pykakasi"
