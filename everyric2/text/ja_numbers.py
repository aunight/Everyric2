"""아라비아 숫자 문자열 → 일본어 자릿수 읽기(히라가나).

배경: UniDic은 아라비아 숫자 토큰(feature.pos2 == "数詞")에 대해 읽기를 주지 않는다 —
``feature.kana``/``feature.pron``이 모두 비어 있어 ``ja_reading._token_readings``의
사다리가 표면(숫자 그대로)으로 떨어진다. 그 결과 ``1秒``가 「いちびょう」가 아니라
「1びょう」로 읽혀 한글 표기가 「이치뵤오」 대신 「1 뵤오」가 된다(실측: 보카로 위키 사람
발음 표본 ``tests/fixtures/wiki_pron_sample.json``의 "スケジュールは1秒先だって..."
줄 — 사람은 「이치뵤오」로 적었다). 한자 숫자(一/二/三…)는 UniDic 사전에 읽기가 이미
있어(一→いち 등) 이 모듈이 필요 없다 — 실측으로 확인했다(``ja_reading._numeral_override``
docstring 참조).

이 모듈은 숫자 자체의 읽기만 만든다. **조수사 앞에서만** 호출하는 결정과 조수사에 따른
음변화(一分→いっぷん의 촉음화·반탁음화)는 ``ja_reading``이 한다 — 이 모듈은 조수사를
모른다. 숫자 자릿수 읽기는 조수사와 무관하게 고정된 문법(백/천의 촉음·반탁음화 포함)이라
"실측 없이 만들지 마라" 원칙의 대상이 아니고, 그래서 조수사 쪽 규칙과 분리해 둔다.

이 모듈이 내는 읽기는 **한자어 수사 계열**(いち·に·さん…)이다. 和語 계열이 붙는 자리
(1日 ついたち, 2つ ふたつ)는 ``ja_reading``이 語種으로 걸러 아예 이 모듈을 부르지 않는다.
"""
from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"^[0-9]+$")

_ONES: dict[int, str] = {
    1: "いち", 2: "に", 3: "さん", 4: "よん", 5: "ご",
    6: "ろく", 7: "なな", 8: "はち", 9: "きゅう",
}

# 百/千 자리는 특정 숫자에서 촉음화·반탁음화가 일어난다(3百→さんびゃく, 6百→ろっぴゃく,
# 8百→はっぴゃく, 3千→さんぜん, 8千→はっせん). 나머지는 규칙대로 자리+숫자를 잇는다.
# 1百/1千은 いち를 붙이지 않는다(百→ひゃく, 千→せん) — 만(万) 이상과 다른 관례다.
_HYAKU_IRREGULAR: dict[int, str] = {3: "さんびゃく", 6: "ろっぴゃく", 8: "はっぴゃく"}
_SEN_IRREGULAR: dict[int, str] = {3: "さんぜん", 8: "はっせん"}

# 10^4 단위 묶음 이름. 이 이상(京 등)은 가사에 나올 일이 없어 다루지 않는다.
_BIG_UNITS = ("", "まん", "おく", "ちょう")


def _read_1_to_9(n: int) -> str:
    return _ONES[n]


def _read_below_10000(n: int) -> str:
    """0 <= n < 10000 인 정수의 자릿수 읽기. n == 0이면 빈 문자열(상위 단위가 없을 때만 호출됨)."""
    if n == 0:
        return ""
    out: list[str] = []

    sen, rest = divmod(n, 1000)
    if sen:
        out.append(_SEN_IRREGULAR.get(sen, ("" if sen == 1 else _read_1_to_9(sen)) + "せん"))

    hyaku, rest = divmod(rest, 100)
    if hyaku:
        out.append(_HYAKU_IRREGULAR.get(hyaku, ("" if hyaku == 1 else _read_1_to_9(hyaku)) + "ひゃく"))

    juu, ichi = divmod(rest, 10)
    if juu:
        out.append(("" if juu == 1 else _read_1_to_9(juu)) + "じゅう")
    if ichi:
        out.append(_read_1_to_9(ichi))

    return "".join(out)


def digits_to_reading(digits: str) -> str | None:
    """아라비아 숫자 문자열을 일본어 기수 읽기(히라가나)로. 처리 불가면 ``None``.

    ``digits``는 ``[0-9]+`` 형태만 받는다(부호·소수점 없음 — UniDic이 数詞로 묶는 토큰이
    이 형태다). 0은 「ゼロ」(현대 일본어에서 압도적으로 우세한 관례). 万 단위를 넘는
    묶음(京 이상)은 가사에 쓰일 일이 없다고 보고 ``None``을 돌려 호출부가 손대지 않게 한다.
    """
    if not digits or not _DIGITS_RE.match(digits):
        return None
    n = int(digits)
    if n == 0:
        return "ゼロ"

    groups: list[int] = []
    while n > 0:
        groups.append(n % 10000)
        n //= 10000
    if len(groups) > len(_BIG_UNITS):
        return None

    parts: list[str] = []
    for idx in range(len(groups) - 1, -1, -1):
        g = groups[idx]
        if g == 0:
            continue
        parts.append(_read_below_10000(g) + _BIG_UNITS[idx])
    return "".join(parts)
