"""가나 → romaji(라틴 문자) 결정적 변환 (miraheze/헵번 관례).

``kana_hangul``이 가나→한글을 결정론으로 하듯, 이 모듈은 가나→romaji를 결정론으로
한다. 표기 관례는 vocaloidlyrics.miraheze.org 등 팬 위키의 대다수 표기(헵번 자음 +
wapuro 장음)를 따른다:

- 헵번 자음: し→shi, ち→chi, つ→tsu, ふ→fu, じ→ji (ぢ도 병합), を→o, ん→n
  (모음·y 앞은 n').
- 촉음(っ)은 **다음 자음의 중복**이다 — 다음 모라 로마자 표기의 첫 글자를 그대로
  겹친다(っか→kka, っしゅ→sshu). 예외로 다음 모라가 ち행(ちゃ/ちゅ/ちょ/ち,
  헵번 표기가 ch로 시작)이면 겹치는 글자는 c가 아니라 **t**다(っち→tchi) — 실제
  겹치는 소리는 /t/이고 miraheze 표기도 이 관례를 따른다.
- 장음은 **매크로 없이** 가나 표기 그대로 직전 모음을 반복한다(wapuro 스타일):
  こう→kou, とお→too, ー(장음부호)는 직전 모음을 반복(コーヒー→koohii). 매크론
  표기(ō/ū)를 쓰지 않는 이유는 miraheze 표기 대다수가 이 방식이기 때문이다.

역할 분담은 ``kana_hangul``·``pron_style``과 같다: 무엇을 읽는가는 ``ja_reading``,
표기 관례만 이 모듈이 담당한다. ``pron_style._kana_run_renderer("romaji")``가
``kana_to_romaji``를 지연 임포트해 가나 런 환전에 쓴다.
"""
from __future__ import annotations

from everyric2.text import kana_hangul

# 요음/외래어 확장 소문자 — 직전 가나와 결합해 1모라(reading.py의 _SMALL_COMBINING과
# 같은 집합).
_SMALL_COMBINING = frozenset("ゃゅょぁぃぅぇぉゎ")

_SOKUON = frozenset({"っ", "ッ"})
_HATSUON = frozenset({"ん", "ン"})
_CHOUON = frozenset({"ー"})

_VOWEL_LETTERS = frozenset("aeiou")

# 오십음 + 탁음/반탁음 단음 (히라가나 기준 — 가타카나는 호출부에서 정규화됨)
_SINGLES: dict[str, str] = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o",
    "ゔ": "vu", "ゎ": "wa", "ゕ": "ka", "ゖ": "ke",
    # 외래어 소문자 단독 출현(결합 상대가 없는 경우의 폴백 근사) — kana_hangul의
    # _SMALL_VOWELS와 같은 근사 방침
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo",
}

# 2글자 조합(요음·외래어 조합) — kana_hangul._DIGRAPHS와 같은 키 집합을 romaji 값으로.
_DIGRAPHS: dict[str, str] = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "ぢゃ": "ja", "ぢゅ": "ju", "ぢょ": "jo",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo", "ふゅ": "fyu",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "しぇ": "she", "じぇ": "je", "ちぇ": "che", "いぇ": "ye",
}

_MORA_ROMAJI: dict[str, str] = {**_SINGLES, **_DIGRAPHS}


def _norm(mora: str) -> str:
    """가타카나 모라를 히라가나로(가나 외 문자는 그대로) — kana_hangul 재사용."""
    return kana_hangul._to_hiragana(mora)


def _last_vowel(romaji: str) -> str:
    """로마자 조각의 마지막 모음 글자 (장음부 ー 환전용). 없으면 빈 문자열."""
    for ch in reversed(romaji):
        if ch in _VOWEL_LETTERS:
            return ch
    return ""


def _peek_romaji(moras: list[str], base: list[str | None], idx: int) -> str:
    """``idx`` 자리 모라의 로마자 스펠링(문맥 판단용 선행 조회).

    이미 계산된 ``base``(특수 모라가 아닌 자리만 채워짐)를 우선 쓰고, 다음 자리도
    특수 모라라 아직 비어 있으면(っ 뒤에 ん 등, 실제로는 거의 없는 배열) 보수적으로
    근사한다 — ん은 자음(n)으로, っ·ー는 문맥이 없어 빈 문자열로 취급한다.
    """
    if idx >= len(moras):
        return ""
    if base[idx] is not None:
        return base[idx]
    m = _norm(moras[idx])
    if m in _HATSUON:
        return "n"
    return ""


def moras_to_romaji(moras: list[str]) -> list[str]:
    """모라 열을 romaji 토큰 열로 변환한다 (입력과 같은 길이) — ``kana_to_romaji``의 정본 구현.

    가나가 아닌 모라(라틴 단어·숫자·공백 등, 표에 없는 문자열)는 원형 그대로 통과한다.
    특수 모라 셋은 이웃을 봐야 정해진다:

    - 촉음(っ): 다음 모라 로마자의 첫 글자를 겹친다. 다음이 ち행(ch로 시작)이면
      예외적으로 t(っち→tchi).
    - 발음(ん): 다음 모라 로마자가 모음이나 y로 시작하면 ``n'``, 아니면(자음·어말)
      그냥 ``n``.
    - 장음부(ー): 직전 모라 로마자의 마지막 모음을 반복한다(매크론 없음).
    """
    base: list[str | None] = []
    for m in moras:
        norm = _norm(m)
        if norm in _SOKUON or norm in _HATSUON or norm in _CHOUON:
            base.append(None)
        else:
            base.append(_MORA_ROMAJI.get(norm, norm))

    out: list[str] = []
    for i, m in enumerate(moras):
        norm = _norm(m)
        if norm in _SOKUON:
            nxt = _peek_romaji(moras, base, i + 1)
            if nxt.startswith("ch"):
                out.append("t")
            else:
                out.append(nxt[0] if nxt else "")
        elif norm in _HATSUON:
            nxt = _peek_romaji(moras, base, i + 1)
            starts_vowel_or_y = bool(nxt) and (nxt[0] in _VOWEL_LETTERS or nxt[0] == "y")
            out.append("n'" if starts_vowel_or_y else "n")
        elif norm in _CHOUON:
            prev = out[i - 1] if i > 0 else ""
            out.append(_last_vowel(prev))
        else:
            out.append(base[i])
    return out


def _split_moras(text: str) -> list[str]:
    """(이미 히라가나로 정규화된) 문자열을 모라 조각으로 쪼갠다.

    요음/외래어 확장 소문자는 직전 조각이 가나일 때만 결합한다(reading.py의
    ``_hira_to_kana_moras``와 같은 규칙, 가나 외 문자가 섞인 문자열까지 다루도록
    일반화). 가나가 아닌 문자(공백·라틴·숫자·부호)는 글자 하나씩 낱개로 통과한다.
    """
    pieces: list[str] = []
    for ch in text:
        cp = ord(ch)
        prev_is_kana = bool(pieces) and 0x3040 <= ord(pieces[-1][-1]) <= 0x30FF
        if ch in _SMALL_COMBINING and prev_is_kana:
            pieces[-1] += ch
            continue
        pieces.append(ch)
    return pieces


def kana_to_romaji(text: str) -> str:
    """가나 문자열을 romaji로 변환한다 (가나 외 문자는 원형 그대로 통과).

    가타카나는 ``kana_hangul._to_hiragana``로 정규화한 뒤 모라로 쪼개
    ``moras_to_romaji``를 적용한다 — 표기 규칙의 단일 구현은 그쪽에 있다.
    """
    if not text:
        return ""
    hira = kana_hangul._to_hiragana(text)
    moras = _split_moras(hira)
    return "".join(moras_to_romaji(moras))
