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

import dataclasses
import re
import unicodedata

from everyric2.text import kana_hangul
from everyric2.text.ja_reading import ReadingToken, tokenize_reading
from everyric2.text.kana_romaji import moras_to_romaji
from everyric2.text.latin_hangul import transliterate_latin
from everyric2.text.reading import text_to_moras

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

# 수사 + 조수사(助数詞)는 한 문절이다 — 위키 사람 발음이 그렇게 적는다(실측,
# tests/fixtures/wiki_pron_sample.json): 「1秒先だって」→「이치뵤오 사키닷테」,
# 「ひとつふたつ白く」→「히토츠 후타츠 시로쿠」. 둘 다 수사와 조수사 사이가 붙어 있다.
#
# 그런데 조수사의 절반은 UniDic에서 접미사가 아니라 **名詞-普通名詞-助数詞可能**이다
# (分・回・階・年・度・秒・時・歩). 名詞는 문절 머리 품사라 그대로 두면 수사와 갈라진다
# — 「이치 뵤오」「난 훈」「이치 호」가 그렇게 나왔다(사용자 제보: 1秒 → 이치 뵤오).
# 접미사로 태깅되는 조수사(本・歳・つ・人)는 이 문제가 없었다.
#
# 조건을 **직전 토큰이 数詞**로 좁힌 것이 중요하다. 助数詞可能은 "조수사로 쓰일 **수도**
# 있는 명사"라는 뜻이어서(度·回·年·時間·分 전부 홀로 쓰는 명사이기도 하다) 이 품사만 보고
# 붙이면 수사 뒤가 아닌 자리에서도 앞말에 붙어 정상 줄의 띄어쓰기가 무너진다 — 「夢 時間
# 世界」의 時間이 앞 명사에 붙는 꼴이다. 語種(한자어/和語)은 보지 않는다 — 붙여 쓰는 것은
# 음운이 아니라 통사의 문제라서 一組(이치쿠미)·1日(이치니치)에도 똑같이 적용된다.
_NUMERAL_POS2 = "数詞"
_COUNTER_POS3 = "助数詞可能"

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
    if prev_pos2 == _NUMERAL_POS2 and token.pos3 == _COUNTER_POS3:
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


def _kana_run_renderer(script: str):
    """가나 런(문절 안에서 이어 붙인 읽기)을 표기 문자로 환전하는 함수를 고른다.

    다국어화(2026-07-28)의 교체 지점이 바로 여기다 — 문절 분해·애매어휘·부호 처리는
    표기와 무관한 공통층이고, 표기가 갈라지는 곳은 «가나 런 환전»과 «라틴 처리»(아래
    ``_render_pronunciation`` 말미) 둘뿐이다. "kana"는 읽기를 그대로 두는 항등이라
    별도 렌더러가 없다(가타카나 표시는 소비처에서 katakana 정규화).
    """
    if script == "hangul":
        return _reading_to_hangul
    if script == "romaji":
        from everyric2.text.kana_romaji import kana_to_romaji

        return kana_to_romaji
    return lambda reading: reading


def _render(pieces: list[_Piece], script: str = "hangul") -> str:
    convert = _kana_run_renderer(script)
    out: list[str] = []
    run: list[str] = []
    for is_kana, chunk in pieces:
        if is_kana:
            run.append(chunk)
            continue
        if run:
            out.append(convert("".join(run)))
            run.clear()
        out.append(chunk)
    if run:
        out.append(convert("".join(run)))
    return "".join(out)


def _render_pronunciation(
    text: str,
    tokens: list[ReadingToken],
    *,
    script: str = "hangul",
    latin_tight: bool = True,
) -> str:
    """이미 만들어진 토큰 열을 위키 표기 관례로 렌더한다 (표기 규칙의 단일 구현).

    토큰 열을 인자로 받는 이유: 같은 라인의 **다른 파스**(N-best·루비 미채택·표층 읽기·
    pykakasi)를 같은 표기 규칙으로 렌더해 오디오 심판의 후보로 쓴다
    (``pronunciation_candidates``). 규칙을 복제하면 후보와 기본값이 표기 관례에서
    갈라져 심판이 "독음 차이"가 아니라 "표기 차이"를 재는 꼴이 된다.
    """
    groups: list[str] = []  # 완성된 문절
    cur: list[_Piece] = []  # 현재 문절
    pending: list[_Piece] = []  # 여는 괄호 등 — 다음 문절의 머리에 붙는다

    def close_group() -> None:
        if cur:
            rendered = _render(cur, script)
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
    for token in tokens:
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
        tail = _render(pending, script)
        if groups:
            groups[-1] += tail
        elif tail:
            groups.append(tail)

    result = _ELLIPSIS_RE.sub("…", " ".join(groups)).replace("・", " ")
    # 라틴 음차는 **여기서** 한다 — 렌더가 끝난 문자열을 보면 형태소 분석기가 쪼갠 조각이
    # 다시 붙어 있어(it / ' / s → "it' s") 낱말 단위로 표를 조회할 수 있다. 독음(읽기)
    # 후보들은 전부 같은 조밀 음차를 공유해 심판이 "독음 차이"만 재게 하고, 라틴 표기
    # 축은 ``latin_tight=False``의 **느슨 후보 하나**로만 갈라진다(``pronunciation_candidates``)
    # — 그 후보와 기본값의 유일한 차이가 라틴 표기여야 심판이 그 축만 재게 된다.
    # 비한글 표기(romaji 등)에서 라틴은 이미 그 표기의 문자다 — 음차 없이 통과한다.
    if script != "hangul":
        return " ".join(result.split())
    return " ".join(transliterate_latin(result, tight=latin_tight).split())


def _has_japanese(text: str) -> bool:
    return bool(text) and (kana_hangul.has_kana(text) or kana_hangul.has_kanji(text))


_LATIN_RE = re.compile(r"[A-Za-z]")


def wiki_pronunciation(text: str, *, script: str = "hangul") -> str:
    """라인의 발음 표기 (위키 표기 관례, 기본은 한글 + 라틴 조밀 음차). 읽을 것이 없으면 빈 문자열.

    ``script``로 표기를 고른다("hangul"|"romaji"|"kana") — 문절 분해·애매어휘 처리는
    표기와 무관하게 공유되고, 마지막 환전만 갈린다(``_kana_run_renderer``).

    **라틴 문자는 한글로 음차한다** (``latin_hangul``). 오래 유지했던 "라틴은 원문 그대로
    둔다"는 방침은 실측으로 뒤집혔다: kor/jpn 어댑터에서 라틴 글자는 정렬되지 않아(라틴
    많은 줄의 라인 conf가 라틴 없는 줄의 1/10, 라틴 글자 conf<0.01이 90~99%) 그 줄의
    타이밍이 통째로 밀린다. 같은 emission에 표기만 바꿔 채점하면 원문 < 관습 음차 <
    조밀 음차 순이고(관습 vs 조밀 7/7), 사람이 만든 일본어 자막 88 cue 대조에서도 잔차
    중앙값 0.085s→0.056s / p90 0.629→0.170s / ±0.3s 80.0%→95.0%로 개선됐다. 표시도 같은
    값을 쓰는 이유는 ``latin_hangul`` 문서에 적어 뒀다(발음 음절 스팬이 표시 발음과
    일치해야 라틴 줄에서도 가라오케 채움이 동작한다).

    라틴만 있는 라인도 음차한다 — 라틴 100% 줄이 바로 정렬이 가장 나쁜 줄이고, 발음이
    비어 있으면 그 줄은 독음(ko) 정렬에 아예 들어가지 못한다. 숫자는 여전히 그대로 둔다
    (1秒 → 「1뵤오」, 사람은 「이치뵤오」 — 별개 문제다).
    """
    if not _has_japanese(text) and not _LATIN_RE.search(text or ""):
        return ""
    return _render_pronunciation(
        text, tokenize_reading(text, phonetic=True, adopt_ruby=True), script=script
    )


# ---------------------------------------------------------------------------
# 애매 어휘 표 — 오디오 심판 후보의 유일한 재료
# ---------------------------------------------------------------------------
#
# 실측(s5Rkv_5Sbbo, 134줄): nbest·pykakasi·루비 미채택·표층 읽기(phonetic=False)를 전부
# 후보 축으로 쓴 이전 구현은 53줄을 갈아치웠고 **53줄 전부 틀렸다**. 원인 셋:
#   1. 삭제가 무료 — 짧은 토큰열이 프레임 평균 점수에서 유리해 11건이 낱말을 그냥 지웠다.
#   2. kor 어댑터가 못 듣는 축 — 21건이 오오/오우 같은 장음 표기 변종이었다. 오디오로
#      가릴 수 없는 축인데 후보에 섞여 신호를 익사시켰다.
#   3. 신뢰도 게이트 없음 — 심판이 들은 것(heard)이 쓰레기여도 채택했다.
#
# 후보를 **텍스트만으로는 결정할 수 없는 낱말**로만 제한하고 재측정하니 8건 맞고 0건
# 틀렸다. 즉 심판 자체가 아니라 후보 생성이 문제였다. 그래서 여기서는 사전/문맥으로
# 못 가르는 것이 확인된 낱말만 표로 등록해 두고, 그 낱말이 실제로 그 줄에 있을 때
# **그 낱말의 대립 읽기 하나만** 후보로 낸다 — 삭제도, 표기 변종도, 무관한 낱말의
# 갈림도 만들지 않는다.
#
# 값은 ``ReadingToken.surface_reading``(표층 읽기, kana 우선)과 비교한다 — phonetic=True
# 음가는 最中처럼 장음을 ー로 뭉개(さいちゅう→さいちゅー) 표 문자열과 어긋난다
# (``_token_readings`` 참조). 표층 읽기는 그 뭉갬이 없어 표와 그대로 맞는다(실측 확인).
#
# 弾く・行く는 여기 없다 — 활용하는 동사라 ``_STEM_AMBIGUOUS_WORDS``(아래)를 쓴다.
#
# 何が도 何も와 같은 축(なに/なん)이다 — 실측(재채점 덤프): 사람 「나니가」/기본값 「난가」로
# 갈렸다. **何て・何で・何か는 표에 넣지 않는다** — 이건 表記가 고정이다(항상 なんて/なんで,
# 何か는 문맥에 관계없이 하나로 굳어 있다). 何만 어간으로 잡아 접두 방식으로 넓히면 이
# 고정 낱말들까지 걸려 전부 오탐이 된다 — 그래서 낱말 전체 일치(何が/何も 각각)로만 잡는다.
_AMBIGUOUS_WORDS: dict[str, tuple[str, str]] = {
    "最中": ("さなか", "さいちゅう"),  # 두 읽기 다 "한창 ~하는 중"
    "好き好き": ("すきすき", "すきずき"),  # 連濁 유무
    "真に": ("まことに", "しんに"),  # 정말로 / 진실로 — 문맥에 따라 갈린다
    "何も": ("なにも", "なんも"),  # 표준 / 축약(회화체)
    "何が": ("なにが", "なんが"),  # 위와 같은 なに/なん 축, が 조사일 때
    "刃": ("は", "やいば"),  # 칼날(짧은 형) / 칼(긴 형, 시적)
    "この期": ("このき", "このご"),  # 관용구 「この期に及んで」의 두 통용 독음
    # 一端 — 사람 발음 코퍼스 실측에서 나왔다: 「一端の何者かに」를 사람은 「잇파시」로 썼고
    # 우리 기본값은 「잇탄」이었다. いっぱし(제법 한몫하는)와 いったん(한쪽 끝) 둘 다 사전에
    # 있고 뜻이 다르다 — 문맥으로 갈려야 하는데 형태소 분석기는 いったん을 1순위로 준다.
    "一端": ("いったん", "いっぱし"),
    # ── 아래 둘은 **사전에 없는 읽기를 후보로 넣는 것**이다. 근거는 사용자 청취뿐이고
    # 오디오는 아직 지지하지 않는다(아래 실측). 넣는 이유는 "오디오가 판정할 기회를 준다,
    # 틀리면 거부된다"이고, 실제로 지금은 거부되므로 **동작이 바뀌지 않는다.** 신호가 강한
    # 곡에서만 고쳐진다.
    #
    # 観て → みえて: 원문 표기는 観る(みる) 계열인데 사용자가 「미에테」로 들었다
    # (8JRuowZtRBc 「ずっと観て」). 見える를 観える로 적는 관행이 J-POP·보카로 가사에 있으나
    # 우리가 받은 가사에는 え가 없다 — 원곡 표기인지 전사 누락인지 확인할 방법이 없었다
    # (그 곡은 사람 자막도 위키 발음도 없고 자동 자막은 전사가 무의미했다).
    # **오디오 실측: 두 지표 모두 기본값을 지지했다**(-0.0518 / -0.0314). 다만 그 줄이 1.2초
    # (59프레임)로 짧고 그리디 전사가 「주더이때」로 쓰레기여서 "판정할 근거가 없다"에 가깝다
    # — 溢れた가 +0.1859로 이겼을 때는 전사에 「고보래」가 들렸다. 신호가 약한 것과 반대
    # 판정인 것은 구별해야 한다.
    "観て": ("みて", "みえて"),
    # 端っこ → すみっこ: 원문은 端っこ(はしっこ, 끝자락)인데 사용자가 「스미코」로 들었다
    # (04L-NZAObiE 「夜の端っこの」). 隅っこ(すみっこ, 구석)와 뜻이 가까워 그렇게 부를 수 있다.
    # **오디오 실측: 짧은 줄에서 +0.0186(마진 0.03 미달), 긴 줄에서 -0.0091** — 역시 판정
    # 근거가 약하다(51프레임, 전사 「욥주니」).
    "端っこ": ("はしっこ", "すみっこ"),
}

# 활용하는 동사 두 항목은 "낱말 전체 문자열"이 아니라 "한자 어간 + 읽기 접두" 짝으로
# 표를 세운다. 실측(4곡 161줄, 실오디오 재채점 덤프로 GPU 없이 재현): 낱말 전체 일치
# 방식은 弾く/行く가 사전형(활용 안 된 꼴)으로 나올 때만 걸려 5줄에서만 후보가 생겼다
# (맞게 2/틀리게 0). 실제로 놓친 줄은 대부분 활용형이었다(弾いて, 行けば 등).
#
# はじく/ひく와 ゆく/いく는 둘 다 か행 활용이라 활용 어미(く/いた/こう/けば/きます…)가
# 두 읽기에서 정확히 똑같이 붙는다(온빈까지 포함: 弾いた→はじいた/ひいた, 行けば→
# ゆけば/いけば). 그래서 **어간 읽기만 바꾸면 활용형이 자동으로 따라온다** — 활용
# 어미 쪽은 원본 토큰의 읽기를 그대로 쓴다.
#
# 값은 (어간 뒤에 오는 읽기 접두 A, 읽기 접두 B)다. 매칭은 토큰의 ``surface_reading``이
# 둘 중 하나로 **시작하는지**만 본다 — 그래서 弾き/弾い/弾か/弾けれ/弾こう 전부 걸린다.
#
# 오탐 방어(실측 확인, ``tests/test_pron_candidates.py`` 참조):
#   - 표층이 한자로 시작해야 한다(``token.surface.startswith(kanji)``) — 銀行(긴코오)의
#     行은 낱말 중간이라 안 걸린다.
#   - 읽기 접두 조건 — 弾む(하즈무)·弾丸(단간)·糾弾(큐우단)·行った(오코난타, 行う의 활용)은
#     읽기가 ひ/はじ・い/ゆ로 시작하지 않아 자동으로 빠진다.
#   - **품사가 動詞여야 한다** — 이게 없으면 弾き語り(히키가타리, 名詞)와 行方(유쿠에,
#     名詞)가 뚫린다. 弾き語り는 읽기가 「히」로, 行方는 「유」로 시작해서 읽기 접두
#     조건만으로는 안 걸러진다(실측으로 찾은 함정) — 둘 다 品詞가 名詞라 動詞 조건으로
#     막는다.
#   - **활용 어미가 촉음(っ)으로 시작하면 行 항목은 후보를 만들지 않는다** — 実측
#     (재채점): 行ったり来たりして에서 만든 「윳타리」 후보가 그 자체로 존재하지 않는
#     읽기였다. 行った/行って/行ったり의 촉음편(いった)은 **いく 전용 활용**이고
#     ゆく의 た형은 문어 ゆきたり다 — 즉 ゆ+った는 애초에 성립하지 않는 활용형이라
#     대입 방향을 만들면 안 된다. 弾く는 이 제외가 필요 없다 — 弾いた(히이타)↔
#     はじいた(하지이타)는 촉음이 아니라 い-온빈이고 둘 다 유효하다(``allow_sokuon``).
_STEM_AMBIGUOUS_WORDS: dict[str, tuple[str, str, bool]] = {
    # (읽기 접두 A, 읽기 접두 B, 촉음(っ)으로 시작하는 활용 어미도 허용하는가)
    "弾": ("ひ", "はじ", True),  # (악기를) 연주하다 / 튕기다 — 둘 다 い-온빈 활용
    "行": ("い", "ゆ", False),  # 구어체 / 문어체·관용구 — 촉음편(いった)은 いく 전용
    # あふれる(넘치다) / こぼれる(흘러넘치다·쏟아지다) — 둘 다 사전에 있는 溢れる의 읽기다.
    # 사용자가 실제 곡을 듣고 찾았다(04L-NZAObiE 「溢れた記憶も」: 우리 기본값 「아후레타」,
    # 실제 가창 「코보레타」). 형태소 분석기는 문맥 없이 あふれ를 1순위로 주는데 텍스트만으로는
    # 가릴 수 없는 축이다 — 정확히 이 표가 존재하는 이유다.
    # 下一段 활용이라 촉음편이 없다(溢れた·溢れて·溢れる 전부 어미가 그대로 붙는다).
    "溢": ("あふ", "こぼ", False),
}


def _covered_tokens(tokens: list[ReadingToken], start: int, end: int) -> list[ReadingToken]:
    """``[start, end)``를 정확히 덮는 연속 토큰열. 경계가 안 맞으면 빈 목록.

    표 낱말이 더 큰 낱말의 부분 문자열일 뿐인 경우(예: 다른 복합어 속의 「刃」)를
    걸러낸다 — 그 복합어가 형태소 분석기에서 한 토큰이면 그 토큰은 ``end``를 넘어서므로
    ``t.end <= end`` 조건에 걸려 뽑히지 않는다.
    """
    covered = [t for t in tokens if t.start >= start and t.end <= end]
    if not covered or covered[0].start != start or covered[-1].end != end:
        return []
    return covered


def _substitute_reading(
    tokens: list[ReadingToken], covered: list[ReadingToken], alternative: str
) -> list[ReadingToken]:
    """``covered``(연속 토큰 구간)의 읽기를 ``alternative`` 하나로 갈아 끼운 새 토큰 열.

    대안 읽기 전체를 첫 토큰에 싣고 나머지 토큰은 읽기·품사를 비운다. 품사를 비우는
    이유: ``_starts_phrase``는 내용어 품사(名詞 등)를 새 문절의 시작으로 본다 — この期
    처럼 두 토큰이 원래 각자 문절 머리인 경우, 품사를 그대로 두면 대안 읽기를 한
    낱말로 합쳐도 "코노 키" 처럼 여전히 갈라진다. 원본 ``tokens``는 손대지 않는다
    (다른 낱말 자리를 채점할 때 이전 치환이 남아 있으면 안 된다 — 후보 하나당 대립
    읽기 하나만 바꾼다는 계약이 깨진다).
    """
    covered_ids = {id(t) for t in covered}
    out: list[ReadingToken] = []
    is_head = True
    for token in tokens:
        if id(token) not in covered_ids:
            out.append(token)
            continue
        if is_head:
            out.append(dataclasses.replace(token, reading=alternative, surface_reading=alternative))
            is_head = False
        else:
            out.append(dataclasses.replace(token, reading="", surface_reading="", pos="", pos2=""))
    return out


def _ambiguous_word_candidates(
    text: str, default_tokens: list[ReadingToken]
) -> list[tuple[str, list[ReadingToken]]]:
    """줄에 있는 애매 어휘마다, 그 낱말만 대립 읽기로 바꾼 후보 (렌더 문자열, 토큰 열) 하나씩.

    다른 모든 토큰(따라서 문절 띄어쓰기·다른 낱말의 독음)은 기본값과 완전히 같다 — 이
    후보와 기본값의 유일한 차이가 그 낱말의 독음이어야 심판이 "독음 차이"만 재게 된다.
    토큰 열을 같이 반환하는 이유는 ``candidate_token_sets``(다국어화, 2026-07-28)가
    심판이 고른 후보의 모라 수를 romaji 등 다른 표기에도 그대로 반영해야 해서다.
    """
    out: list[tuple[str, list[ReadingToken]]] = []
    for word, (reading_a, reading_b) in _AMBIGUOUS_WORDS.items():
        search_from = 0
        while True:
            idx = text.find(word, search_from)
            if idx < 0:
                break
            end = idx + len(word)
            search_from = end
            covered = _covered_tokens(default_tokens, idx, end)
            if not covered or "".join(t.surface for t in covered) != word:
                continue
            current = "".join(t.surface_reading for t in covered)
            if current == reading_a:
                alternative = reading_b
            elif current == reading_b:
                alternative = reading_a
            else:
                # 기본 읽기가 표의 둘 중 어느 쪽도 아니다(활용형 등 자리가 안 맞음) —
                # 대입 방향을 짐작하지 않는다.
                continue
            candidate_tokens = _substitute_reading(default_tokens, covered, alternative)
            rendered = _render_pronunciation(text, candidate_tokens)
            if rendered:
                out.append((rendered, candidate_tokens))
    return out


def _stem_word_candidates(
    text: str, default_tokens: list[ReadingToken]
) -> list[tuple[str, list[ReadingToken]]]:
    """줄에 있는 활용 동사(``_STEM_AMBIGUOUS_WORDS``)마다, 어간 읽기만 바꾼 (렌더 문자열,
    토큰 열) 후보 하나씩.

    토큰 하나만 바꾼다(``_substitute_reading``에 단일 토큰 구간을 넘긴다) — 활용 어미는
    같은 토큰에 붙어 있는 채로 남으므로 손대지 않는다. 弾く/行く는 활용해도 어미 앞
    토큰 하나에 어간+활용부가 함께 들어오므로(예: 弾いた → 토큰 "弾い" + "た") 이걸로
    충분하다.
    """
    out: list[tuple[str, list[ReadingToken]]] = []
    for kanji, (prefix_a, prefix_b, allow_sokuon) in _STEM_AMBIGUOUS_WORDS.items():
        for token in default_tokens:
            if token.pos != "動詞" or not token.surface.startswith(kanji):
                continue
            reading = token.surface_reading
            if reading.startswith(prefix_a):
                matched, alt_prefix = prefix_a, prefix_b
            elif reading.startswith(prefix_b):
                matched, alt_prefix = prefix_b, prefix_a
            else:
                # 읽기가 표의 두 어간 중 어느 쪽도 아니다(弾む·行った(行う) 등 동음 다른
                # 낱말) — 대입 방향을 짐작하지 않는다.
                continue
            suffix = reading[len(matched):]
            if not allow_sokuon and suffix.startswith("っ"):
                # 行った類의 촉음편은 いく 전용 활용이라 ゆ 쪽에 대응하는 형태가 존재하지
                # 않는다 — 대입 방향을 만들지 않는다(위 표 주석 참조).
                continue
            alternative = alt_prefix + suffix
            candidate_tokens = _substitute_reading(default_tokens, [token], alternative)
            rendered = _render_pronunciation(text, candidate_tokens)
            if rendered:
                out.append((rendered, candidate_tokens))
    return out


def _pronunciation_candidates_with_tokens(
    text: str, *, max_candidates: int = 8
) -> list[tuple[str, list[ReadingToken]]]:
    """``pronunciation_candidates``/``candidate_token_sets``의 공유 구현.

    (렌더 문자열, 그 문자열을 만든 토큰 열) 쌍의 목록을 만든다 — 두 공개 함수는 이 중
    한쪽 요소만 뽑아 반환할 뿐, 순서·중복 제거·개수 상한 로직은 여기 하나뿐이다
    (다국어화, 2026-07-28: romaji 등 다른 표기가 심판이 고른 읽기의 모라 수를 그대로
    따르려면 렌더 문자열과 토큰 열이 같은 순서로 짝지어져 있어야 한다).
    """
    if not _has_japanese(text) or max_candidates <= 0:
        return []

    default_tokens = tokenize_reading(text, phonetic=True, adopt_ruby=True)
    default = _render_pronunciation(text, default_tokens)
    if not default:
        # 기본값을 못 만드는 라인은 후보 비교의 기준이 없다 — 심판 대상이 아니다.
        return []

    out: list[tuple[str, list[ReadingToken]]] = [(default, default_tokens)]
    rendered_set = {default}
    for candidate, candidate_tokens in (
        *_ambiguous_word_candidates(text, default_tokens),
        *_stem_word_candidates(text, default_tokens),
    ):
        if len(out) >= max_candidates:
            break
        if candidate not in rendered_set:
            out.append((candidate, candidate_tokens))
            rendered_set.add(candidate)
    # 라틴 느슨(비조밀) 후보 — **음절이 늘어나는 방향** 하나만. 조밀이 실측 기본값이지만
    # 가수가 접힌 음절을 다 부르는 곡이 있다(사용자 청취 vg6pnvn1u10: 테익이 아니라
    # 테이크). tighten은 접기/버리기만 하므로 이 후보는 기본값보다 음절이 항상 많거나
    # 같다 — 예전 심판을 망친 삭제 편향(음절 지운 후보가 평평한 posterior에서 공짜로
    # 이김)과 무관한 축이라 안전하다. 독음은 기본값과 동일해 유일한 차이가 라틴 표기다.
    if len(out) < max_candidates and _LATIN_RE.search(text):
        loose = _render_pronunciation(text, default_tokens, latin_tight=False)
        if loose and loose not in rendered_set:
            out.append((loose, default_tokens))
            rendered_set.add(loose)
    return out


def pronunciation_candidates(text: str, *, max_candidates: int = 8) -> list[str]:
    """오디오 심판(``ctc_engine``)에 넘길 후보 독음 목록. ``[0]``이 기본값이다.

    **후보는 ``_AMBIGUOUS_WORDS``/``_STEM_AMBIGUOUS_WORDS`` 표에 있는 낱말의 대립 읽기로만
    제한된다.** 예전에는 nbest·pykakasi·루비 미채택·표층 읽기(phonetic=False)까지 전부
    후보 축으로 썼지만, 실오디오 검증에서 그 구현은 해로웠다(위 표 앞의 실측 참고) —
    삭제가 섞이고, kor 어댑터가 못 듣는 표기 변종이 섞여 신호를 익사시켰다. 표로
    제한하고 재측정하니 8건 맞고 0건 틀렸다. 사전은 표의 낱말이 어느 쪽으로 읽히는지
    모르지만(문맥·표기만으로는 원리적으로 못 가른다) 오디오는 안다 — 그래서 그 축만
    후보로 남긴다.

    기본값(``[0]``)은 항상 ``phonetic=True`` + 루비 채택(``wiki_pronunciation``과 완전히
    동일)이다. 그 뒤에 줄에서 실제로 걸린 애매 낱말마다 **그 낱말만** 대립 읽기로 바꾼
    후보를 하나씩 붙인다(활용 동사는 어간만, 나머지는 낱말 전체). 중복은 제거하고
    순서를 보존한다.

    반환이 1개면(또는 빈 목록이면) **후보 없음**이며 호출부는 심판을 아예 돌리지 않는다
    → 비용 0. 애매 낱말이 없는 압도적 다수의 줄이 이 경우다. 일본어가 없는 라인은
    빈 목록(``wiki_pronunciation``은 라틴만 있는 줄도 음차를 내지만, 후보의 존재 이유인
    "낱말 독음의 갈림"이 라틴 줄엔 없다).

    실제 순서·중복 제거·개수 상한 로직은 ``_pronunciation_candidates_with_tokens``
    하나뿐이다 — 이 함수는 그 렌더 문자열만 뽑는다(``candidate_token_sets``는 토큰
    열까지 병렬로 반환한다). 골든 스냅샷(``tests/test_pron_golden.py``)이 반환값의
    문자열·순서가 이 리팩터링 전후로 바이트 동일함을 보장한다.
    """
    return [
        rendered
        for rendered, _ in _pronunciation_candidates_with_tokens(text, max_candidates=max_candidates)
    ]


def candidate_token_sets(text: str) -> tuple[list[str], list[list[ReadingToken]]]:
    """``pronunciation_candidates``와 같은 순서로 (렌더 문자열 목록, 토큰 열 목록) 병렬 반환.

    ``rendered == pronunciation_candidates(text)``가 항상 성립한다 — 같은 공유 구현
    (``_pronunciation_candidates_with_tokens``)을 쓰기 때문이다. ``tokens[i]``는
    ``rendered[i]``를 만든 토큰 열이며, 오디오 심판이 ``chosen`` 인덱스를 고르면
    ``tokens[chosen]``을 ``reading.text_to_moras(text, tokens=...)``에 넘겨 그 읽기의
    모라 수를 romaji 등 다른 표기에도 그대로 반영할 수 있다. ``[0]``이 기본값이다.
    """
    pairs = _pronunciation_candidates_with_tokens(text)
    return [rendered for rendered, _ in pairs], [tokens for _, tokens in pairs]


# ---------------------------------------------------------------------------
# romaji 라인 렌더 — 표시=모라 토큰 단일 소스
# ---------------------------------------------------------------------------

# 이 토큰 뒤에는 romaji 관용 표기에서도 공백을 넣지 않는다 — 활용의 연속이지 새
# 낱말이 아니다. 조동사(た/ます/ない 등)·접미사는 pos1만으로 충분하고, 접속조사(て)는
# 동사 활용을 잇는 붙임씨라 pos2까지 봐야 한다(係助詞·格助詞인 は/が/を 등은 로마자
# 관용 표기에서 독립된 낱말로 띄운다 — 실측: 「アルバイトはネクラモード」→
# "arubaito wa nekura moodo", は가 독립 단어로 뜬다. pron_style의 _CONJUNCTIVE_PARTICLE_POS2
# 와 같은 판단축이다).
_ROMAJI_GLUE_POS1 = frozenset({"助動詞", "接尾辞"})
_ROMAJI_GLUE_PARTICLE_POS2 = "接続助詞"


def _romaji_glues_to_previous(token: ReadingToken) -> bool:
    """이 토큰이 romaji 표기에서 앞말에 그대로 붙는가(활용의 연속)."""
    if token.pos in _ROMAJI_GLUE_POS1:
        return True
    return token.pos == "助詞" and token.pos2 == _ROMAJI_GLUE_PARTICLE_POS2


def _token_owning(tokens: list[ReadingToken], char_pos: int) -> ReadingToken | None:
    """``char_pos``를 포함하는 토큰 (표면이 원문 전 구간을 이어 붙이므로 항상 찾는다)."""
    for token in tokens:
        if token.start <= char_pos < token.end:
            return token
    return None


def romaji_line(
    text: str, tokens: list[ReadingToken] | None = None
) -> tuple[str, list[str], list[bool]] | None:
    """라인의 romaji 표시 — (표시 문자열, 모라 romaji 토큰 열, 모라 뒤 공백 여부).

    **표시=세그 단일 소스** 불변식을 보장한다:
    ``display == "".join(tok + (" " if sp else "") for tok, sp in zip(moras, spaces)).strip()``.

    ``tokens``를 주면(오디오 심판이 고른 읽기 — ``candidate_token_sets`` 참조) 그
    읽기로 모라를 만든다(``reading.text_to_moras``); 생략하면 ``wiki_pronunciation``과
    같은 기본 읽기(``phonetic=True, adopt_ruby=True``)로 새로 토큰화한다.

    공백 규칙은 위키 한글 표기의 문절(文節) 규칙과 다른 축이다 — romaji는 형태소
    **토큰** 경계로 판단한다: 다음 모라가 새 토큰에서 왔고 그 토큰이 활용의 연속
    (조동사·접미사·접속조사, ``_romaji_glues_to_previous``)이 아니면 공백, 원문에서
    공백·구두점으로 건너뛴 글자 구간이 있어도 공백. 라틴/ASCII 모라는
    ``moras_to_romaji``의 표 미등록 통과 경로를 타 원형 그대로 나온다.
    """
    if not _has_japanese(text) and not _LATIN_RE.search(text):
        return None
    if tokens is None:
        tokens = tokenize_reading(text, phonetic=True, adopt_ruby=True)

    moras = text_to_moras(text, tokens=tokens)
    if not moras:
        return None

    romaji_tokens = moras_to_romaji([m.kana for m in moras])

    spaces: list[bool] = []
    for i in range(len(moras) - 1):
        cur, nxt = moras[i], moras[i + 1]
        gap = cur.char_end < nxt.char_start
        owner_cur = _token_owning(tokens, cur.char_start)
        owner_nxt = _token_owning(tokens, nxt.char_start)
        new_token = owner_cur is not owner_nxt
        grammatical = new_token and owner_nxt is not None and not _romaji_glues_to_previous(owner_nxt)
        spaces.append(grammatical or gap)
    spaces.append(False)

    display = "".join(tok + (" " if sp else "") for tok, sp in zip(romaji_tokens, spaces)).strip()
    return display, romaji_tokens, spaces
