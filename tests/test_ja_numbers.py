"""아라비아 숫자 자릿수 읽기(everyric2.text.ja_numbers) 단위 테스트.

여기서 검증하는 자릿수 읽기 자체(백/천의 촉음화·반탁음화, 만 단위 앞의 いち 유지 등)는
어느 조수사가 뒤따르는지와 무관한 고정 문법이라 실측(코퍼스) 없이도 테스트한다.
"조수사 앞에서 언제 이 모듈을 부르는가"의 실측 근거는 ``test_ja_reading.py``의
``_numeral_override`` 관련 테스트에 있다(``ja_reading._MEASURED_ARABIC_COUNTERS`` 참조).
"""
import pytest

from everyric2.text.ja_numbers import digits_to_reading

# ---------------------------------------------------------------------------
# 1. 한 자리 수 (0~9)
# ---------------------------------------------------------------------------

_ONE_DIGIT_CASES = [
    ("0", "ゼロ"),
    ("1", "いち"),
    ("2", "に"),
    ("3", "さん"),
    ("4", "よん"),
    ("5", "ご"),
    ("6", "ろく"),
    ("7", "なな"),
    ("8", "はち"),
    ("9", "きゅう"),
]


@pytest.mark.parametrize(("digits", "expected"), _ONE_DIGIT_CASES)
def test_single_digit_readings(digits, expected):
    assert digits_to_reading(digits) == expected


# ---------------------------------------------------------------------------
# 2. 십의 자리 — 十은 단독일 때 いち를 붙이지 않는다
# ---------------------------------------------------------------------------

_TENS_CASES = [
    ("10", "じゅう"),
    ("11", "じゅういち"),
    ("20", "にじゅう"),
    ("24", "にじゅうよん"),
    ("99", "きゅうじゅうきゅう"),
]


@pytest.mark.parametrize(("digits", "expected"), _TENS_CASES)
def test_tens_place_readings(digits, expected):
    assert digits_to_reading(digits) == expected


# ---------------------------------------------------------------------------
# 3. 백/천의 자리 — 3·6·8에서 촉음화·반탁음화, 1은 いち를 붙이지 않는다
# ---------------------------------------------------------------------------

_HUNDREDS_THOUSANDS_CASES = [
    ("100", "ひゃく"),
    ("300", "さんびゃく"),  # 촉음화+반탁음화: さんひゃく가 아니다
    ("600", "ろっぴゃく"),  # 촉음화+반탁음화: ろくひゃく가 아니다
    ("800", "はっぴゃく"),  # 촉음화+반탁음화: はちひゃく가 아니다
    ("105", "ひゃくご"),
    ("1000", "せん"),
    ("3000", "さんぜん"),  # 반탁음화: さんせん이 아니다
    ("8000", "はっせん"),  # 촉음화+반탁음화: はちせん이 아니다
    ("2024", "にせんにじゅうよん"),
]


@pytest.mark.parametrize(("digits", "expected"), _HUNDREDS_THOUSANDS_CASES)
def test_hundreds_and_thousands_readings(digits, expected):
    assert digits_to_reading(digits) == expected


# ---------------------------------------------------------------------------
# 4. 만/억 단위 — 千·百와 달리 1을 いち로 명시한다(一万 = いちまん, 万이 아니다)
# ---------------------------------------------------------------------------


def test_man_unit_keeps_ichi_unlike_hyaku_and_sen():
    assert digits_to_reading("10000") == "いちまん"
    assert digits_to_reading("30000") == "さんまん"
    assert digits_to_reading("100000000") == "いちおく"


# ---------------------------------------------------------------------------
# 5. 입력 계약 — 숫자가 아니거나 처리 범위를 넘으면 None (호출부가 기존 동작 유지)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("digits", ["", "abc", "1.5", "-1", "1a", " 1"])
def test_invalid_input_returns_none(digits):
    assert digits_to_reading(digits) is None


def test_beyond_chou_returns_none():
    # 京(10^16) 이상은 가사에 쓰일 일이 없다고 보고 다루지 않는다
    assert digits_to_reading("1" + "0" * 16) is None
