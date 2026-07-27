"""ko_reading: 한국어 → 가타카나(hangul_to_kana)·RR 로마자(hangul_to_romaja) +
라인 모라 분해(hangul_line_moras) + 라틴→가나 체인(latin_to_kana).

기대값은 국립국어원 표준 로마자 표기법(RR)과 한글→가타카나 관용 표기(평음 ㄱ·ㄷ·ㅂ·ㅈ의
어두/장애음뒤=무성, 모음/공명음뒤=유성 교대 포함)로 손계산해 확정했다 — 구현에 맞춘 값이
아니다. 연음(먹어→머거)·ㅎ탈락(좋아→조아, 복합받침 ㄶ 포함)만 v1 범위이며, 그 근거는
``ko_reading`` 모듈 docstring에 있다.
"""
import pytest

from everyric2.text.ko_reading import (
    hangul_line_moras,
    hangul_line_romaja_syllables,
    hangul_to_kana,
    hangul_to_romaja,
    latin_to_kana,
)


@pytest.mark.parametrize(
    "ko,kana",
    [
        ("사랑해", "サランヘ"),
        ("먹어", "モゴ"),  # 연음(먹어→머거) 후 ㄱ이 모음 뒤(유성)라 ゴ
        ("있잖아", "イッチャナ"),  # ㄶ의 ㅎ탈락+ㄴ연음(잖아→자나), ㅈ이 받침ㅆ뒤(무성)라 チャ
        ("좋아", "チョア"),  # ㅎ탈락(좋아→조아), ㅈ이 어두(무성)라 チョ
        ("한국", "ハングク"),  # 연음 없음, ㄱ이 받침ㄴ뒤(유성)라 グ, 종성ㄱ은 ク
    ],
)
def test_hangul_to_kana(ko, kana):
    assert hangul_to_kana(ko) == kana


def test_hangul_to_kana_passthrough_non_hangul():
    assert hangul_to_kana("Take it easy!") == "Take it easy!"
    assert hangul_to_kana("사랑해!") == "サランヘ!"


@pytest.mark.parametrize(
    "ko,rr",
    [
        ("사랑해", "saranghae"),
        ("먹어", "meogeo"),  # 연음 후 RR은 위치 무관 g 고정 — 유성 교대 불필요
        ("한국", "hanguk"),
        ("흘러", "heulleo"),  # 설측음화: 받침ㄹ+초성ㄹ → 둘 다 l
        ("신라", "silla"),  # 설측음화: 받침ㄴ+초성ㄹ → 둘 다 l (ㄹ만이 아니라 ㄴ도 동화)
    ],
)
def test_hangul_to_romaja(ko, rr):
    assert hangul_to_romaja(ko) == rr


def test_hangul_to_romaja_passthrough_non_hangul():
    assert hangul_to_romaja("hello") == "hello"


def test_latin_to_kana_chain():
    # latin_hangul.transliterate_latin("take", tight=False) == "테이크"(느슨 음차)
    # → hangul_to_kana("테이크") == "テイク"
    assert latin_to_kana("take") == "テイク"


def test_hangul_line_moras_coda_becomes_two_morae_same_span():
    # 한 = cho ㅎ + jung ㅏ + jong ㄴ → 받침 ㄴ이 독립 가나 ン이 되어 2모라,
    # 두 모라 모두 같은 (char_start, char_end) = (0, 1)을 공유한다.
    moras = hangul_line_moras("한")
    assert moras == [("ハ", 0, 1), ("ン", 0, 1)]


def test_hangul_line_moras_open_syllable_is_one_mora():
    # 사 = cho ㅅ + jung ㅏ + 받침 없음 → 1모라만.
    moras = hangul_line_moras("사")
    assert moras == [("サ", 0, 1)]


def test_hangul_line_moras_full_line_char_spans():
    # 사랑해: 사(1모라)+랑(2모라: ラ,ン 같은 span 1..2)+해(1모라)
    moras = hangul_line_moras("사랑해")
    assert moras == [
        ("サ", 0, 1),
        ("ラ", 1, 2),
        ("ン", 1, 2),
        ("ヘ", 2, 3),
    ]


def test_hangul_line_romaja_syllables_matches_hangul_to_romaja():
    # 사랑해 = sa+rang+hae (연음 없음), 각 음절 1글자 1스팬 — kana처럼 2모라로 갈리지 않는다.
    assert hangul_line_romaja_syllables("사랑해") == [
        ("sa", 0, 1), ("rang", 1, 2), ("hae", 2, 3),
    ]
    # 먹어 = 연음(먹어→머거) 후 meo+geo
    assert hangul_line_romaja_syllables("먹어") == [
        ("meo", 0, 1), ("geo", 1, 2),
    ]
    # 흘러 = 설측음화(heulleo)까지 음절 함수에도 반영돼야 한다 — heul+leo
    assert hangul_line_romaja_syllables("흘러") == [
        ("heul", 0, 1), ("leo", 1, 2),
    ]
    # 이어붙이면 hangul_to_romaja와 항상 같다(같은 내부 함수를 공유하므로)
    for text in ("사랑해", "먹어", "흘러", "신라"):
        assert "".join(s for s, _, _ in hangul_line_romaja_syllables(text)) == hangul_to_romaja(text)


def test_hangul_line_romaja_syllables_passthrough_non_hangul():
    assert hangul_line_romaja_syllables("사 랑!") == [("sa", 0, 1), ("rang", 2, 3), ("!", 3, 4)]


def test_hangul_line_moras_skips_space_passes_through_punctuation():
    moras = hangul_line_moras("사 랑!")
    assert moras == [("サ", 0, 1), ("ラ", 2, 3), ("ン", 2, 3), ("!", 3, 4)]
