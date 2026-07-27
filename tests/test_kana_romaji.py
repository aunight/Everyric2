"""가나 → romaji 결정적 변환(everyric2.text.kana_romaji) 회귀 테스트.

표기 관례(miraheze 대다수 표기): 헵번 자음 + wapuro 장음(매크론 없음). 촉음은
다음 자음 중복, ん은 모음·y 앞에서 n', 장음부 ー는 직전 모음 반복.
"""
import pytest

from everyric2.text.kana_romaji import kana_to_romaji, moras_to_romaji


@pytest.mark.parametrize("kana,expected", [
    ("ずっと", "zutto"), ("しゅんかん", "shunkan"), ("ちょっとまって", "chottomatte"),
    ("スーパー", "suupaa"), ("こんや", "kon'ya"), ("せんえん", "sen'en"),
    ("フラッシュバック", "furasshubakku"), ("っち", "tchi"),
    ("あいしあえる", "aishiaeru"), ("わ たし", "wa tashi"),  # 공백 통과
    ("abc123", "abc123"),  # 비가나 통과
])
def test_kana_to_romaji(kana, expected):
    assert kana_to_romaji(kana) == expected


def test_moras_to_romaji_same_length_and_sokuon():
    # ずっと → [zu, t, to]: っ은 다음 자음 하나를 받는다
    assert moras_to_romaji(["ず", "っ", "と"]) == ["zu", "t", "to"]
    # っち는 tchi — っ 토큰이 t, ち가 chi
    assert moras_to_romaji(["ま", "っ", "ち"]) == ["ma", "t", "chi"]
    # 장음 ー는 직전 모음
    assert moras_to_romaji(["す", "ー"]) == ["su", "u"]
    # ん 단독 모라, 다음이 모음이면 n'
    assert moras_to_romaji(["せ", "ん", "え", "ん"]) == ["se", "n'", "e", "n"]
    # 요음 결합 모라 1개
    assert moras_to_romaji(["しゅ", "ん"]) == ["shu", "n"]
