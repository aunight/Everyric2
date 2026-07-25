"""일본어 라인 → 보카로 위키 관례의 한글 발음 표기 (결정론).

발음 표기를 LLM에게 묻지 않기 위한 모듈이다. 실측(보카로 위키의 사람이 쓴 발음
2,207줄 대비 완전일치율): pykakasi 70.7% → fugashi ``feature.kana`` 72.0% →
``feature.pron``(음가) 75.5% → ``えい`` 규칙 81.0% → ``ふぃ`` 81.2% → 루비 채택 81.9%
(CER≤0.1이 89.5%). 같은 곡 624줄에서 LLM 경로 82.2% vs 이 경로 82.4%로 사실상
동등한데, LLM은 같은 줄을 실행마다 다르게 읽고(「縋って」를 3회 중 2회 오독, 「恋愛」를
코이아이) 조사 は를 표층대로 "하"라고 쓰는 실수를 반복한다. 결정론 경로는 재현 가능하고,
발음을 프롬프트에서 빼면 출력 토큰이 절반 이하로 줄어 번역 배치를 키울 수 있다.

역할 분담은 기존과 같다: "무엇을 어떻게 읽는가"는 ``ja_reading``, "가나→한글"은
``kana_hangul``. 이 모듈은 그 둘 위에 **위키 표기 관례**만 얹는다.
"""
from __future__ import annotations

import re
import unicodedata

from everyric2.text import kana_hangul
from everyric2.text.ja_reading import ReadingToken, tokenize_reading

# ---------------------------------------------------------------------------
# 문절(文節) 띄어쓰기
# ---------------------------------------------------------------------------

# 새 문절을 시작하는 품사(UniDic pos1) — 내용어(自立語). 조사·조동사·접미사·기호는
# 부속어라 앞 문절에 붙는다. 위키는 발음을 문절 단위로 띄어 쓴다:
# 叫んだ音は既に列を成さないで → "사켄다 오토와 스데니 레츠오 나사나이데"
# (한 덩어리로 붙이면 읽을 수 없다).
_PHRASE_HEAD_POS = frozenset(
    {
        "名詞",
        "代名詞",
        "動詞",
        "形容詞",
        "形状詞",  # UniDic의 형용동사(な형용사)
        "形容動詞",
        "副詞",
        "連体詞",
        "接続詞",
        "感動詞",
        "接頭辞",
        "フィラー",
    }
)

# 접두사는 뒤따르는 내용어와 한 문절을 이룬다 — お+母+さん은 "오 카상"이 아니라 "오카상".
_PREFIX_POS = "接頭辞"

# 補助動詞·サ変동사 판정. pos1만 보면 いる·しまう·する가 動詞라 문절이 갈라지는데
# (맛테 이루 / 코쿠하쿠 스루요), 위키는 앞 문절에 붙여 쓴다(맛테이루 / 코쿠하쿠스루요).
# 다만 非自立可能은 본동사로 쓰인 来る·いる에도 붙으므로(なにかが来ている,
# ここにいるの → 위키도 여기선 띄운다) 앞 토큰이 접속조사(〜て)나 명사(サ変)일 때만
# 부속어로 본다.
_AUX_VERB_POS2 = "非自立可能"
_CONJUNCTIVE_PARTICLE_POS2 = "接続助詞"
_SAHEN_HOST_POS = "名詞"

# 조동사 어간(そう/よう의 형상詞)도 부속어다 — なりますように → "나리마스요오니",
# 楽しそうね → "타노시소오네". pos1이 形状詞라 그대로 두면 문절이 갈라진다.
_AUX_STEM_POS2 = "助動詞語幹"

# 홀로는 음절이 못 되는 모라 — 문절의 머리에 설 수 없다. UniDic이 じゃん의 ん을
# 感動詞/フィラー로 잘라 놓는 경우가 있어(超ロックじゃん) 그대로 두면 "자 응"이 된다.
_NON_INITIAL_MORAS = frozenset("んっー")

# ---------------------------------------------------------------------------
# 표기 관례
# ---------------------------------------------------------------------------

# 위키는 전각 문장부호를 반자로 적는다. 위치는 원문 그대로 유지한다.
_PUNCT_NORMALIZE = {
    "？": "?",
    "！": "!",
    "、": ",",
    "，": ",",
    "。": ".",
    "（": "(",
    "）": ")",
    "：": ":",
    "；": ";",
    "～": "~",
}

# 이 부호 뒤에서는 문절이 끊긴다 — だ！よ！ね！チュ！ → "다! 요! 네! 추!",
# 体、蝕む → "카라다, 무시바무".
_BREAK_AFTER = frozenset("!?,.")

# 나카구로(・)는 발음이 없다. 연달아 오면 줄임표로, 하나면 단어 구분(공백)으로 적는 것이
# 위키 관례다: ならば・・・ → "나라바…", クレイジー・インザ・タウン → "쿠레이지이 인 자 타운".
_ELLIPSIS_RE = re.compile("・{2,}")

# 위키 관례: ふぃ/ふぇ는 피/페가 아니라 휘/훼로 적는다(실측 +0.2p,
# カタストロフィー → 카타스토로휘이). ``kana_hangul``의 표는 표준 표기(피/페)를 쓰고
# 모라 정렬·LLM 응답 마감이 그 값을 공유하므로 여기서만 갈아 끼운다.
#
# 치환값이 완성된 한글 음절인 것은 의도된 것이다 — ``kana_to_hangul``은 가나가 아닌
# 글자를 그대로 통과시키고, 장음(ー)·촉음(っ)·ん은 "직전 출력 음절"만 보고 처리하므로
# 미리 한글로 바꿔 넣어도 뒤따르는 ー/っ/ん이 정상 동작한다(ふぃー → 휘 + 이).
_WIKI_DIGRAPHS = {"ふぃ": "휘", "ふぇ": "훼"}

def _restore_ei(reading: str, surface_reading: str) -> str:
    """음가가 ``エイ``를 장음으로 뭉갠 자리만 ``い``로 되돌린다 (토큰 단위).

    위키는 ``えい``를 "에이"로 적는데 ``feature.pron``은 음가라 ``エー``를 주고, 그 장음이
    직전 모음을 반복해 "에에"가 된다. 되살릴 자리는 표층 읽기(``feature.kana``)와 맞춰
    보면 정확히 나온다: 鮮明 kana=センメイ / pron=センメー → 3번째가 원래 ``イ``다.
    실측(코퍼스 2,207줄 완전일치, 장음 정규화 기준선 80.0%): 이 정밀화로 +2.6p → 82.6%.

    한글 단계에서 "ㅔ/ㅖ + 받침 없는 음절 뒤의 에"를 바꾸던 앞선 구현은 진짜 장음까지
    건드렸다 — ``ねー``→네이(사람 네에), ``かっけー``→캇케이(캇케에),
    ``バースデー``→바아스데이(바아스데에). 그 세 경우는 표층 읽기에도 ``ー``가 있으므로
    아래 조건에서 자연히 제외된다.

    - 두 읽기의 길이가 다르면(활용·미등록어로 자리가 안 맞으면) 손대지 않는다.
    - 표층 읽기에 ``ー``가 있으면 원래부터 장음인 말이므로 손대지 않는다.
    - ``イ``만 되살린다. ``ウ``는 제외다 — ``オウ``를 "오우"로 만들면 위키의 압도적 다수인
      "오오"(一緒→잇쇼오, 対象→타이쇼오)에서 멀어진다.
    """
    if len(reading) != len(surface_reading) or "ー" in surface_reading:
        return reading
    return "".join(
        "い" if r == "ー" and s == "い" else r
        for r, s in zip(reading, surface_reading)
    )


def _reading_to_hangul(reading: str) -> str:
    for kana, hangul in _WIKI_DIGRAPHS.items():
        reading = reading.replace(kana, hangul)
    return kana_hangul.kana_to_hangul(reading)


def _is_punctuation(surface: str) -> bool:
    """토큰 표면이 전부 부호인가 (품사 대신 글자로 판정 — 폴백 경로엔 품사가 없다)."""
    return all(unicodedata.category(ch)[0] in ("P", "S") for ch in surface)


def _is_opening(ch: str) -> bool:
    """여는 괄호·따옴표인가 (Ps/Pi). 이 부호는 앞이 아니라 **다음** 문절에 붙는다 —
    歌う「ひとりじゃない」는 "우타우「 히토리자"가 아니라 "우타우 「히토리자"다."""
    return unicodedata.category(ch) in ("Ps", "Pi")


def _starts_phrase(token: ReadingToken, prev_pos: str, prev_pos2: str) -> bool:
    """이 토큰에서 새 문절이 시작하는가."""
    if token.pos not in _PHRASE_HEAD_POS or token.pos2 == _AUX_STEM_POS2:
        return False
    if prev_pos == _PREFIX_POS:
        return False
    if token.reading and all(ch in _NON_INITIAL_MORAS for ch in token.reading):
        return False
    if token.pos == "動詞" and token.pos2 == _AUX_VERB_POS2:
        # 補助動詞(〜て + いる/しまう/くる)와 サ変동사(명사 + する)는 부속어로 붙는다.
        # 명사 조건은 pos3=サ変可能까지 보지 않아 조금 넓다 — UniDic이 忘れる 같은 본동사도
        # 非自立可能으로 달아 두기 때문에 "판츠와스레타"처럼 과하게 붙는 줄이 남는다.
        if prev_pos2 == _CONJUNCTIVE_PARTICLE_POS2 or prev_pos == _SAHEN_HOST_POS:
            return False
    return True


# 문절 조각: (가나 읽기인가, 내용). 가나는 문절이 끝날 때 **인접한 것끼리 이어 붙여**
# 한 번에 변환한다 — 토큰별로 변환하면 토큰 경계에 걸린 ん·っ이 앞 음절을 못 찾아
# 받침이 되지 못한다(いたい|ん|だ → "이타이응다", だ|って → "다테").
_Piece = tuple[bool, str]


def _render(pieces: list[_Piece]) -> str:
    out: list[str] = []
    run: list[str] = []
    for is_kana, chunk in pieces:
        if is_kana:
            run.append(chunk)
            continue
        if run:
            out.append(_reading_to_hangul("".join(run)))
            run.clear()
        out.append(chunk)
    if run:
        out.append(_reading_to_hangul("".join(run)))
    return "".join(out)


def wiki_pronunciation(text: str) -> str:
    """일본어 라인의 위키식 한글 발음 표기. 일본어가 없으면 빈 문자열.

    라틴 문자·숫자는 **음차하지 않고 그대로 둔다**. 위키는 음차하지만(numb→넘,
    Beat→비이토) 규칙화가 불가능하고(원어 발음 지식이 필요하다), 실측에서 라틴을 포함한
    줄은 어느 경로로도 4.1%만 맞아 별도 문제로 남긴다.
    """
    if not text or not (kana_hangul.has_kana(text) or kana_hangul.has_kanji(text)):
        return ""

    groups: list[str] = []  # 완성된 문절
    cur: list[_Piece] = []  # 현재 문절
    pending: list[_Piece] = []  # 여는 괄호 등 — 다음 문절의 머리에 붙는다

    def close_group() -> None:
        if cur:
            rendered = _render(cur)
            if rendered:
                groups.append(rendered)
            cur.clear()

    def add(piece: _Piece, *, new_group: bool) -> None:
        if new_group:
            close_group()
        if not cur and pending:
            cur.extend(pending)
            pending.clear()
        cur.append(piece)

    prev_pos = prev_pos2 = ""
    for token in tokenize_reading(text, phonetic=True, adopt_ruby=True):
        surface = token.surface
        if not surface.strip():
            # 원문의 공백은 문절 경계다 (위키도 그 자리를 띄운다)
            close_group()
            prev_pos = prev_pos2 = ""
            continue

        if _is_punctuation(surface):
            # 부호는 읽지 않고 원문 위치에 그대로(반자로) 남긴다
            for ch in surface:
                norm = _PUNCT_NORMALIZE.get(ch, ch)
                if _is_opening(ch):
                    close_group()
                    pending.append((False, norm))
                    continue
                (cur if cur else pending).append((False, norm))
                if norm in _BREAK_AFTER:
                    close_group()
            prev_pos = prev_pos2 = ""
            continue

        # 루비 채택으로 읽기가 비워진 한자 뭉치는 문절만 끊고 아무것도 적지 않는다
        new_group = _starts_phrase(token, prev_pos, prev_pos2)
        if token.reading:
            add(
                (True, _restore_ei(token.reading, token.surface_reading)),
                new_group=new_group,
            )
        elif new_group:
            close_group()
        prev_pos, prev_pos2 = token.pos, token.pos2

    close_group()
    if pending:
        # 라인 끝에 남은 부호(닫는 괄호 등)를 흘리면 원문 문자가 사라진다 — 마지막 문절에 붙인다
        tail = _render(pending)
        if groups:
            groups[-1] += tail
        elif tail:
            groups.append(tail)

    result = _ELLIPSIS_RE.sub("…", " ".join(groups)).replace("・", " ")
    return " ".join(result.split())
