"""한국어 가사 → 일본어권(가타카나)·영어권(RR 로마자) 사용자용 읽기 엔진.

``kana_hangul``(가나→한글)·``kana_romaji``(가나→로마자)의 반대 방향이다: 여기서는
한글이 출발점이고 도착점이 가타카나 또는 로마자다. 자모 분해는 ``reading.py``의
``_decompose_hangul``을 그대로 재사용한다(복사·이동 금지 — 정의는 그쪽이 정본).

## 적용한 음운 규칙 (v1 — 이 둘만)

1. **연음**: 받침(단일 자음)이 있는 글자 뒤에 초성이 ㅇ(모음으로 시작)인 글자가 오면
   받침이 다음 글자의 초성으로 옮겨간다(먹어→머거→모고). ㅇ 받침(비음 /ŋ/)은 옮겨갈
   초성 자리가 없으므로 연음 대상이 아니다(표준 한국어 음운론과 동일).
2. **ㅎ 약화(탈락)**: 받침 ㅎ 뒤에 초성 ㅇ인 글자가 오면 ㅎ은 자리를 옮기지 않고
   사라진다(좋아→조아). 복합받침 중 ㅎ이 뒤에 오는 것(ㄶ·ㅀ)도 같다 — ㅎ만 탈락하고
   앞 자음(ㄴ·ㄹ)이 연음으로 다음 초성이 된다(있잖아의 "잖아"→"자나").

그 외(경음화·격음화·복합받침의 비ㅎ 자음 연음 등)는 **일부러 넣지 않았다** — 과잉
규칙이 틀리면 표기 그대로보다 나쁘다는 것이 이 작업의 전제다. 연음·ㅎ탈락으로
해소되지 않은 복합받침(ㄳ·ㄵ·ㄺ·ㄻ·ㄼ·ㄽ·ㄾ·ㄿ·ㅄ)은 받침 표기용으로만 표준
중화음(닭→ㄱ, 삶→ㅁ 등 표준발음법의 대표음)으로 근사한다 — 이 근사는 실측 검증이
없는 안전망이다.

## 가타카나 표기의 유성/무성 교대

한국어 평음 ㄱ·ㄷ·ㅂ·ㅈ은 어두·장애음 뒤에서는 무성(カ·タ·パ·チャ 행), 모음이나
공명음(ㄴ·ㄹ·ㅁ·ㅇ) 뒤(=유성 환경)에서는 유성(ガ·ダ·バ·ジャ 행)으로 실현된다 —
실제 한국어 음운론의 평음 유성화와 정확히 같은 조건이다. 이 교대를 넣지 않으면
한국(ハングク — ㄱ이 ㄴ 뒤라 유성 グ)·있잖아(イッチャナ — ㅈ이 받침 ㅆ 뒤라 무성
チャ) 둘 다 표준 표기와 어긋난다. ㅋ·ㅌ·ㅍ·ㅊ(격음)과 ㄲ·ㄸ·ㅃ·ㅆ·ㅉ(경음)은
위치와 무관하게 항상 같은 행(무성)을 쓴다.

## RR(국립국어원 로마자 표기법)

초성·중성·종성 세 표를 그대로 쓴다 — RR은 초성 ㄱ/ㄷ/ㅂ/ㅈ을 위치와 무관하게
g/d/b/j로 고정하므로(한국=Hanguk의 "국"이 g로 시작하는 것이 그 예) 가타카나처럼
유성/무성 문맥을 볼 필요가 없다. 연음은 동일하게 먼저 적용한다(발음 기반 표기가
표준이므로).
"""
from __future__ import annotations

from everyric2.text.latin_hangul import transliterate_latin
from everyric2.text.reading import _decompose_hangul

# ---------------------------------------------------------------------------
# 중성(모음) 분류 — (활음, 모음군). ㅢ는 별도 처리(합성 표기)라 센티널 사용.
# ---------------------------------------------------------------------------

_JUNG_INFO: dict[str, tuple[str | None, str | None]] = {
    "ㅏ": (None, "a"), "ㅐ": (None, "e"), "ㅑ": ("y", "a"), "ㅒ": ("y", "e"),
    "ㅓ": (None, "o"), "ㅔ": (None, "e"), "ㅕ": ("y", "o"), "ㅖ": ("y", "e"),
    "ㅗ": (None, "o"), "ㅘ": ("w", "a"), "ㅙ": ("w", "e"), "ㅚ": ("w", "e"),
    "ㅛ": ("y", "o"), "ㅜ": (None, "u"), "ㅝ": ("w", "o"), "ㅞ": ("w", "e"),
    "ㅟ": ("w", "i"), "ㅠ": ("y", "u"), "ㅡ": (None, "u"), "ㅢ": ("ui", None),
    "ㅣ": (None, "i"),
}

# ---------------------------------------------------------------------------
# 가타카나 — 초성 행 (무성/기본)
# ---------------------------------------------------------------------------

_CHO_ROWS: dict[str, dict[str, str]] = {
    "ㄱ": {"a": "カ", "i": "キ", "u": "ク", "e": "ケ", "o": "コ"},
    "ㄲ": {"a": "ッカ", "i": "ッキ", "u": "ック", "e": "ッケ", "o": "ッコ"},
    "ㄴ": {"a": "ナ", "i": "ニ", "u": "ヌ", "e": "ネ", "o": "ノ"},
    "ㄷ": {"a": "タ", "i": "ティ", "u": "トゥ", "e": "テ", "o": "ト"},
    "ㄸ": {"a": "ッタ", "i": "ッティ", "u": "ットゥ", "e": "ッテ", "o": "ット"},
    "ㄹ": {"a": "ラ", "i": "リ", "u": "ル", "e": "レ", "o": "ロ"},
    "ㅁ": {"a": "マ", "i": "ミ", "u": "ム", "e": "メ", "o": "モ"},
    "ㅂ": {"a": "パ", "i": "ピ", "u": "プ", "e": "ペ", "o": "ポ"},
    "ㅃ": {"a": "ッパ", "i": "ッピ", "u": "ップ", "e": "ッペ", "o": "ッポ"},
    "ㅅ": {"a": "サ", "i": "シ", "u": "ス", "e": "セ", "o": "ソ"},
    "ㅆ": {"a": "ッサ", "i": "ッシ", "u": "ッス", "e": "ッセ", "o": "ッソ"},
    "ㅇ": {"a": "ア", "i": "イ", "u": "ウ", "e": "エ", "o": "オ"},
    "ㅈ": {"a": "チャ", "i": "チ", "u": "チュ", "e": "チェ", "o": "チョ"},
    "ㅉ": {"a": "ッチャ", "i": "ッチ", "u": "ッチュ", "e": "ッチェ", "o": "ッチョ"},
    "ㅊ": {"a": "チャ", "i": "チ", "u": "チュ", "e": "チェ", "o": "チョ"},
    "ㅋ": {"a": "カ", "i": "キ", "u": "ク", "e": "ケ", "o": "コ"},
    "ㅌ": {"a": "タ", "i": "ティ", "u": "トゥ", "e": "テ", "o": "ト"},
    "ㅍ": {"a": "パ", "i": "ピ", "u": "プ", "e": "ペ", "o": "ポ"},
    "ㅎ": {"a": "ハ", "i": "ヒ", "u": "フ", "e": "ヘ", "o": "ホ"},
}

# 평음 ㄱ·ㄷ·ㅂ·ㅈ의 유성 변이형 (모음/공명음 뒤에서만 쓴다 — 아래 _LENIS_VOICING)
_CHO_ROWS_VOICED: dict[str, dict[str, str]] = {
    "ㄱ": {"a": "ガ", "i": "ギ", "u": "グ", "e": "ゲ", "o": "ゴ"},
    "ㄷ": {"a": "ダ", "i": "ディ", "u": "ドゥ", "e": "デ", "o": "ド"},
    "ㅂ": {"a": "バ", "i": "ビ", "u": "ブ", "e": "ベ", "o": "ボ"},
    "ㅈ": {"a": "ジャ", "i": "ジ", "u": "ジュ", "e": "ジェ", "o": "ジョ"},
}

_LENIS_VOICING = frozenset({"ㄱ", "ㄷ", "ㅂ", "ㅈ"})

# 요음(ㅑㅠㅛ류)·외래어 활음(ㅘㅝ류) 결합용 소형 가나. 초성이 ㅇ(공초성)이면 직접형을 쓴다
# (야→ヤ, 와→ワ 등 — 기존 가나에 이미 있는 표기라 イャ·ウァ 같은 합성을 피한다).
_SMALL_Y = {"a": "ャ", "u": "ュ", "o": "ョ", "e": "ェ"}
_SMALL_W = {"a": "ァ", "o": "ォ", "e": "ェ", "i": "ィ"}
_NULL_Y_DIRECT = {"a": "ヤ", "u": "ユ", "o": "ヨ", "e": "イェ"}
_NULL_W_DIRECT = {"a": "ワ", "o": "ウォ", "e": "ウェ", "i": "ウィ"}

# 받침(종성) → 독립 가나. ㅎ과 빈 받침은 표에 없다(= "" 묵음 처리, dict.get 기본값).
_CODA_KANA: dict[str, str] = {
    "ㄴ": "ン", "ㅇ": "ン",
    "ㅁ": "ム",
    "ㄹ": "ル",
    "ㅂ": "プ", "ㅍ": "プ",
    "ㄱ": "ク", "ㅋ": "ク", "ㄲ": "ク",
    "ㅅ": "ッ", "ㅆ": "ッ", "ㄷ": "ッ", "ㅌ": "ッ", "ㅈ": "ッ", "ㅊ": "ッ",
}

# ---------------------------------------------------------------------------
# RR(국립국어원 로마자 표기법) — 초성/중성/종성 표
# ---------------------------------------------------------------------------

_RR_ONSET: dict[str, str] = {
    "ㄱ": "g", "ㄲ": "kk", "ㄴ": "n", "ㄷ": "d", "ㄸ": "tt", "ㄹ": "r",
    "ㅁ": "m", "ㅂ": "b", "ㅃ": "pp", "ㅅ": "s", "ㅆ": "ss", "ㅇ": "",
    "ㅈ": "j", "ㅉ": "jj", "ㅊ": "ch", "ㅋ": "k", "ㅌ": "t", "ㅍ": "p", "ㅎ": "h",
}

_RR_VOWEL: dict[str, str] = {
    "ㅏ": "a", "ㅐ": "ae", "ㅑ": "ya", "ㅒ": "yae", "ㅓ": "eo", "ㅔ": "e",
    "ㅕ": "yeo", "ㅖ": "ye", "ㅗ": "o", "ㅘ": "wa", "ㅙ": "wae", "ㅚ": "oe",
    "ㅛ": "yo", "ㅜ": "u", "ㅝ": "wo", "ㅞ": "we", "ㅟ": "wi", "ㅠ": "yu",
    "ㅡ": "eu", "ㅢ": "ui", "ㅣ": "i",
}

_RR_CODA: dict[str, str] = {
    "ㄱ": "k", "ㄲ": "k", "ㄴ": "n", "ㄷ": "t", "ㄹ": "l", "ㅁ": "m",
    "ㅂ": "p", "ㅅ": "t", "ㅆ": "t", "ㅇ": "ng", "ㅈ": "t", "ㅊ": "t",
    "ㅋ": "k", "ㅌ": "t", "ㅍ": "p",
}

# ---------------------------------------------------------------------------
# 연음 + ㅎ탈락 (공유 — 가타카나·로마자 렌더 모두 이 결과를 쓴다)
# ---------------------------------------------------------------------------

# 복합받침 → (첫 자음, 둘째 자음). ㅎ이 둘째인 것(ㄶ·ㅀ)만 연음에서 특별 취급한다.
_COMPLEX_JONG: dict[str, tuple[str, str]] = {
    "ㄳ": ("ㄱ", "ㅅ"), "ㄵ": ("ㄴ", "ㅈ"), "ㄶ": ("ㄴ", "ㅎ"),
    "ㄺ": ("ㄹ", "ㄱ"), "ㄻ": ("ㄹ", "ㅁ"), "ㄼ": ("ㄹ", "ㅂ"),
    "ㄽ": ("ㄹ", "ㅅ"), "ㄾ": ("ㄹ", "ㅌ"), "ㄿ": ("ㄹ", "ㅍ"),
    "ㅀ": ("ㄹ", "ㅎ"), "ㅄ": ("ㅂ", "ㅅ"),
}

# 연음·ㅎ탈락으로 해소되지 못한 복합받침의 받침 표기용 근사(표준발음법의 대표음).
# 실측 검증 없는 안전망 — 닭·삶·값 같은 낱말이 나왔을 때 크래시 대신 근사값을 낸다.
_COMPLEX_JONG_SURFACE: dict[str, str] = {
    "ㄳ": "ㄱ", "ㄵ": "ㄴ", "ㄶ": "ㄴ", "ㄺ": "ㄱ", "ㄻ": "ㅁ",
    "ㄼ": "ㄹ", "ㄽ": "ㄹ", "ㄾ": "ㄹ", "ㄿ": "ㅂ", "ㅀ": "ㄹ", "ㅄ": "ㅂ",
}

# 연음으로 다음 글자 초성 자리로 옮겨갈 수 있는 단일 받침(ㅇ·ㅎ은 제외 — 각각 비음이라
# 옮겨갈 자리가 없거나(ㅇ) ㅎ탈락 규칙이 따로 처리한다).
_MOVABLE_JONG = frozenset("ㄱㄲㄴㄷㄹㅁㅂㅅㅆㅈㅊㅋㅌㅍ")

# 유성 환경을 만드는 받침(공명음) — 빈 받침("", 즉 모음으로 끝남)도 포함.
_SONORANT_JONG = frozenset({"", "ㄴ", "ㄹ", "ㅁ", "ㅇ"})


def _liaise(run: list[list]) -> None:
    """(초성,중성,종성,글자위치) 리스트에 연음+ㅎ탈락을 제자리 적용한다.

    ``run``은 공백·비한글로 끊기지 않은 한글 글자 구간 하나다(연음은 같은 낱말
    안에서만 일어난다 — 띄어쓰기를 건너 적용하지 않는다).
    """
    for i in range(len(run) - 1):
        cho, jung, jong, idx = run[i]
        if not jong:
            continue
        nxt = run[i + 1]
        if nxt[0] != "ㅇ":
            continue
        if jong == "ㅎ":
            run[i][2] = ""
        elif jong in _COMPLEX_JONG:
            first, second = _COMPLEX_JONG[jong]
            if second == "ㅎ":
                run[i][2] = ""
                nxt[0] = first
            # else: ㅎ이 아닌 복합받침은 v1 범위 밖 — 표기 근사로 남긴다(과욕 금지).
        elif jong in _MOVABLE_JONG:
            nxt[0] = jong
            run[i][2] = ""


def _hangul_runs(text: str):
    """``text``를 (한글 여부, payload) 조각으로 나눈다.

    한글 조각의 payload는 ``[cho, jung, jong, char_idx]`` 리스트(``_liaise``가
    제자리로 바꿀 수 있게 가변 리스트로 둔다), 비한글 조각은 ``(char, char_idx)``.
    """
    i, n = 0, len(text)
    while i < n:
        if _decompose_hangul(text[i]) is not None:
            run: list[list] = []
            while i < n and _decompose_hangul(text[i]) is not None:
                cho, jung, jong = _decompose_hangul(text[i])
                run.append([cho, jung, jong, i])
                i += 1
            yield True, run
        else:
            yield False, (text[i], i)
            i += 1


def _syllable_kana(cho: str, jung: str, voiced: bool) -> str:
    """음절 하나(받침 제외)의 가타카나. 받침은 ``_CODA_KANA``가 별도로 붙인다."""
    glide, vowel = _JUNG_INFO[jung]
    use_voiced = voiced and cho in _LENIS_VOICING
    row = _CHO_ROWS_VOICED[cho] if use_voiced else _CHO_ROWS[cho]

    if glide == "ui":
        return "ウイ" if cho == "ㅇ" else row["u"] + "イ"
    if glide is None:
        return row[vowel]
    if glide == "y":
        return _NULL_Y_DIRECT[vowel] if cho == "ㅇ" else row["i"] + _SMALL_Y[vowel]
    # glide == "w"
    if cho == "ㅇ":
        return "ワ" if vowel == "a" else _NULL_W_DIRECT[vowel]
    return row["u"] + _SMALL_W[vowel]


def _render_run(run: list[list]) -> list[tuple[str, str, int]]:
    """연음 적용 후 음절별 (초성+중성 가나, 받침 가나 또는 "", 글자위치)."""
    _liaise(run)
    out: list[tuple[str, str, int]] = []
    context = "voiceless"
    for cho, jung, jong, idx in run:
        onset_vowel = _syllable_kana(cho, jung, context == "voiced")
        eff_jong = _COMPLEX_JONG_SURFACE.get(jong, jong)
        coda = _CODA_KANA.get(eff_jong, "")
        out.append((onset_vowel, coda, idx))
        context = "voiced" if eff_jong in _SONORANT_JONG else "voiceless"
    return out


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------


def hangul_to_kana(text: str) -> str:
    """한국어 텍스트를 가타카나 발음 표기로 바꾼다. 비한글 문자는 그대로 통과."""
    out: list[str] = []
    for is_hangul, payload in _hangul_runs(text):
        if is_hangul:
            for onset_vowel, coda, _idx in _render_run(payload):
                out.append(onset_vowel)
                out.append(coda)
        else:
            out.append(payload[0])
    return "".join(out)


def _rr_liquid_overrides(run: list[list]) -> tuple[list[bool], list[bool]]:
    """설측음화(받침 ㄴ·ㄹ + 다음 초성 ㄹ → 둘 다 "l") 대상 표시.

    표준 로마자 표기법은 받침 ㄹ+초성 ㄹ(흘러→heulleo)뿐 아니라 받침 ㄴ+초성 ㄹ도
    같은 [ll]로 동화된다고 본다(신라→[실라]→silla). ``_liaise``(ㅇ이 방아쇠)가 끝난
    뒤의 jong/cho를 보므로 서로 다른 방아쇠(ㅇ vs ㄹ) 규칙이라 간섭하지 않는다.

    반환: (그 인덱스의 받침을 "l"로 적을지, 그 인덱스의 초성을 "l"로 적을지) 두 불리언
    리스트 — 각각 ``run``과 길이가 같다.
    """
    n = len(run)
    coda_l = [False] * n
    onset_l = [False] * n
    for i in range(n - 1):
        jong = run[i][2]
        next_cho = run[i + 1][0]
        if next_cho == "ㄹ" and jong in ("ㄴ", "ㄹ"):
            coda_l[i] = True
            onset_l[i + 1] = True
    return coda_l, onset_l


def hangul_to_romaja(text: str) -> str:
    """한국어 텍스트를 국립국어원 로마자 표기법으로 바꾼다. 비한글 문자는 그대로 통과."""
    out: list[str] = []
    for is_hangul, payload in _hangul_runs(text):
        if is_hangul:
            _liaise(payload)
            coda_l, onset_l = _rr_liquid_overrides(payload)
            for k, (cho, jung, jong, _idx) in enumerate(payload):
                eff_jong = _COMPLEX_JONG_SURFACE.get(jong, jong)
                onset = "l" if onset_l[k] else _RR_ONSET.get(cho, "")
                coda = "l" if coda_l[k] else _RR_CODA.get(eff_jong, "")
                out.append(onset)
                out.append(_RR_VOWEL.get(jung, ""))
                out.append(coda)
        else:
            out.append(payload[0])
    return "".join(out)


def hangul_line_moras(text: str) -> list[tuple[str, int, int]]:
    """원문 한글 라인을 (모라 토큰, char_start, char_end) 리스트로 분해한다.

    한글 1글자는 기본 1모라. 받침이 독립 가나(ン/ッ/ム/ル/ク/プ)로 실현되면 그 글자에
    2모라가 귀속되고, 둘 다 같은 (char_start, char_end)를 공유한다(한→ハ+ン). 공백은
    모라를 만들지 않고, 그 외 비한글 문자(구두점 등)는 낱글자 1모라로 통과한다.
    """
    result: list[tuple[str, int, int]] = []
    for is_hangul, payload in _hangul_runs(text):
        if is_hangul:
            for onset_vowel, coda, idx in _render_run(payload):
                result.append((onset_vowel, idx, idx + 1))
                if coda:
                    result.append((coda, idx, idx + 1))
        else:
            ch, idx = payload
            if not ch.isspace():
                result.append((ch, idx, idx + 1))
    return result


def latin_to_kana(text: str) -> str:
    """라틴 문자열 → (``latin_hangul``의 느슨 음차) → 가타카나 체인.

    느슨(tight=False) 음차를 쓰는 이유: 조밀 음차(tight=True)는 CTC 정렬용으로 어말
    자음을 받침으로 접는데(take→테익), 그 받침을 다시 가나로 펼치면 받침 표기(ク·プ
    등)가 섞여 원래 관습 발음(테이크)과 달라진다. 여기는 표시 전용이라 관습형(느슨
    음차)이 사람이 읽기에 더 자연스럽다. 검증된 두 표(라틴→한글 음차, 한글→가나)를
    그대로 이어 쓰는 것이 v1 전략이다 — 별도의 영어 발음 사전이 필요 없다.
    """
    return hangul_to_kana(transliterate_latin(text, tight=False))
