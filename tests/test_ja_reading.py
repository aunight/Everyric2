"""형태소 분석 기반 일본어 읽기(everyric2.text.ja_reading) 회귀 테스트.

no-mock: fugashi + unidic-lite 실제 분석 결과를 그대로 쓴다. 케이스는 pykakasi 사전
읽기가 실제로 오독했던 가사 라인들이다(今更止められない→"やめ", 縋って→"つい"って).

폴백 테스트만 예외적으로 태거 생성을 몽키패치한다 — fugashi가 없는 환경을 실제로
만들 수 없기 때문이며, 검증 대상은 읽기 품질이 아니라 계약(형태/오프셋) 유지다.
"""
import pytest

from everyric2.text import ja_reading
from everyric2.text.ja_reading import (
    ReadingToken,
    kana_reading,
    reading_source,
    tokenize_reading,
)
from everyric2.text.reading import text_to_moras


@pytest.fixture
def kakasi_fallback(monkeypatch):
    """태거 생성을 실패시켜 pykakasi 폴백 경로를 강제한다."""

    def boom():
        raise ImportError("no fugashi in this environment")

    monkeypatch.setattr(ja_reading, "_create_tagger", boom)
    monkeypatch.setattr(ja_reading, "_tagger", None)
    monkeypatch.setattr(ja_reading, "_tagger_unavailable", False)
    yield
    # 몽키패치가 풀려도 캐시된 '사용 불가' 플래그가 남으면 이후 테스트가 폴백에 갇힌다
    monkeypatch.setattr(ja_reading, "_tagger", None)
    monkeypatch.setattr(ja_reading, "_tagger_unavailable", False)


# ---------------------------------------------------------------------------
# 1. 실측 오독 케이스 — 문맥 의존 훈독
# ---------------------------------------------------------------------------

# (원문, 읽기에 반드시 들어가야 하는 부분, 들어가면 안 되는 오독)
_READING_CASES = [
    ("今更止められない", "いまさらとめられない", "やめ"),
    ("縋って", "すがって", "つい"),
    ("涙を止める", "なみだをとめる", "やめる"),
    ("風が止む", "かぜがやむ", "とむ"),
    ("目立って", "めだって", None),
    ("叶えたい", "かなえたい", None),
    ("取り計らって", "とりはからって", None),
    ("巷で流行りの", "ちまたではやりの", None),
    ("君にだけ", "きみにだけ", "くん"),
]


@pytest.mark.parametrize(("text", "expected", "misread"), _READING_CASES)
def test_reading_matches_context_dependent_truth(text, expected, misread):
    reading = kana_reading(text)
    assert reading == expected
    if misread is not None:
        assert misread not in reading


def test_stop_verb_is_disambiguated_by_particle():
    # 같은 止 한자가 조사에 따라 갈린다 — 사전 표제어만으로는 못 하는 판별
    assert "とめる" in kana_reading("涙を止める")
    assert "やむ" in kana_reading("風が止む")


# ---------------------------------------------------------------------------
# 2. 계약: 표면 이어 붙이기 = 원문 복원
# ---------------------------------------------------------------------------

_ROUNDTRIP_LINES = [
    "縋って いつも縋って",
    "ゆーて お坊っちゃんお嬢ちゃん",
    "Don't Stop！ 2024年の夏",
    "君にだけ、そう…「止められない」！",
    "  先頭と末尾に空白  ",
    "アルバイトはネクラモード",
    "",
]


@pytest.mark.parametrize("text", _ROUNDTRIP_LINES)
def test_surfaces_concatenate_back_to_the_original(text):
    tokens = tokenize_reading(text)
    assert "".join(t.surface for t in tokens) == text


@pytest.mark.parametrize("text", _ROUNDTRIP_LINES)
def test_surfaces_concatenate_back_to_the_original_on_fallback(text, kakasi_fallback):
    tokens = tokenize_reading(text)
    assert "".join(t.surface for t in tokens) == text


def test_whitespace_survives_as_a_literal_token():
    # MeCab은 공백을 버린다 — 리터럴 토큰으로 복원되지 않으면 이후 오프셋이 전부 밀린다
    text = "縋って いつも縋って"
    tokens = tokenize_reading(text)
    space = [t for t in tokens if t.surface == " "]
    assert len(space) == 1
    assert space[0].reading == " "
    assert (space[0].start, space[0].end) == (3, 4)


# ---------------------------------------------------------------------------
# 3. 계약: 오프셋 정확성
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", _ROUNDTRIP_LINES)
def test_token_offsets_point_at_their_own_surface(text):
    pos = 0
    for token in tokenize_reading(text):
        assert text[token.start : token.end] == token.surface
        assert token.start == pos  # 구간은 빈틈 없이 이어진다
        pos = token.end
    assert pos == len(text)


# ---------------------------------------------------------------------------
# 4. text_to_moras 연동 — 모라별 원문 오프셋
# ---------------------------------------------------------------------------


def test_moras_keep_original_char_offsets_across_a_space():
    text = "縋って いつも縋って"
    moras = text_to_moras(text)
    assert [m.kana for m in moras] == [
        "す", "が", "っ", "て", "い", "つ", "も", "す", "が", "っ", "て",
    ]
    # 공백은 모라를 만들지 않고 글자 인덱스만 건너뛴다
    assert (moras[0].char_start, moras[0].char_end) == (0, 2)   # 縋っ
    assert (moras[3].char_start, moras[3].char_end) == (2, 3)   # て
    assert (moras[4].char_start, moras[4].char_end) == (4, 6)   # いつ (공백 뒤)
    assert (moras[7].char_start, moras[7].char_end) == (7, 9)   # 縋っ (두 번째)
    assert (moras[-1].char_start, moras[-1].char_end) == (9, 10)
    # 모라 구간은 원문 범위를 넘지 않고 순서대로 증가한다
    assert all(0 <= m.char_start < m.char_end <= len(text) for m in moras)
    assert [m.char_start for m in moras] == sorted(m.char_start for m in moras)


def test_moras_of_a_misread_line_follow_the_correct_reading():
    # 독음이 틀리면 모라 열이 틀어져 발음 타이밍이 어긋난다 — とめ 기준으로 나와야 한다
    moras = text_to_moras("今更止められない")
    assert [m.kana for m in moras] == [
        "い", "ま", "さ", "ら", "と", "め", "ら", "れ", "な", "い",
    ]


def test_ascii_tokens_stay_single_units_with_correct_offsets():
    text = "Don't Stop！ 2024年"
    moras = text_to_moras(text)
    ascii_moras = [m for m in moras if m.is_ascii]
    assert [m.kana for m in ascii_moras] == ["Don't", "Stop", "2024"]
    for m in ascii_moras:
        assert text[m.char_start : m.char_end] == m.kana
    # 年은 일본어 토큰으로 읽힌다
    assert moras[-1].kana == "ん"
    assert [m.kana for m in moras if not m.is_ascii] == ["ね", "ん"]


# ---------------------------------------------------------------------------
# 5. fugashi 없는 환경 폴백 — 계약 동일
# ---------------------------------------------------------------------------


def test_reading_source_reports_the_engine_in_use():
    assert reading_source() == "fugashi"


def test_reading_source_reports_pykakasi_on_fallback(kakasi_fallback):
    assert reading_source() == "pykakasi"


def test_fallback_keeps_the_token_contract(kakasi_fallback):
    text = "縋って いつも縋って"
    tokens = tokenize_reading(text)

    assert tokens and all(isinstance(t, ReadingToken) for t in tokens)
    assert "".join(t.surface for t in tokens) == text
    for token in tokens:
        assert text[token.start : token.end] == token.surface
        assert isinstance(token.reading, str) and token.reading
    # 폴백 경로는 사전 읽기라 오독한다(つい) — 그래도 형태·오프셋 계약은 지킨다
    assert "つい" in kana_reading(text)


def test_fallback_moras_still_map_to_original_chars(kakasi_fallback):
    text = "縋って いつも縋って"
    moras = text_to_moras(text)
    assert moras
    assert all(text[m.char_start : m.char_end] for m in moras)
    assert all(0 <= m.char_start < m.char_end <= len(text) for m in moras)


# ---------------------------------------------------------------------------
# 6. 장음부(ー)·촉음(っ) — 모라 분해에서 각각 1박을 차지해야 한다
# ---------------------------------------------------------------------------


def test_long_vowel_mark_survives_as_its_own_mora():
    # UniDic kana는 가타카나(コーヒー) — 히라가나로 내려가도 ー는 그대로 남고 1모라다
    assert kana_reading("コーヒー") == "こーひー"
    moras = text_to_moras("コーヒー")
    assert [m.kana for m in moras] == ["こ", "ー", "ひ", "ー"]


def test_sokuon_survives_as_its_own_mora():
    assert kana_reading("行った") == "いった"
    moras = text_to_moras("行った")
    assert [m.kana for m in moras] == ["い", "っ", "た"]
    # 촉음은 활용 어간 토큰(行っ)에 붙어 그 글자 구간을 공유한다
    assert (moras[1].char_start, moras[1].char_end) == (0, 2)
    assert (moras[2].char_start, moras[2].char_end) == (2, 3)


def test_katakana_reading_is_lowered_to_hiragana():
    # 요음 ャ도 ゃ로 내려가 직전 가나와 결합해 1모라가 된다
    assert kana_reading("テレキャスター") == "てれきゃすたー"
    assert [m.kana for m in text_to_moras("テレキャスター")] == [
        "て", "れ", "きゃ", "す", "た", "ー",
    ]


def test_long_vowel_and_sokuon_in_one_line():
    moras = text_to_moras("コーヒー行った")
    kanas = [m.kana for m in moras]
    assert kanas.count("ー") == 2
    assert kanas.count("っ") == 1


# ---------------------------------------------------------------------------
# 가타카나 표층 읽기 / 홀로 선 접두사 — 위키 사람 발음 288줄 실측으로 찾은 오독 2종
# ---------------------------------------------------------------------------


def test_katakana_surface_beats_the_dictionary():
    """가타카나는 표음 문자라 사전을 조회하면 오히려 틀린다.

    실측(위키 사람 발음 288줄): UniDic이 エグい를 えぎい로 읽어 「에기이요」가 나왔고
    (정답 「에구이요」), レイニー의 pron이 れーにー로 장음을 뭉개 「레에니이」가 됐다
    (정답 「레이니이」). 두 오독이 독음오류 48줄 중 9줄이었다.
    """
    from everyric2.text.pron_style import wiki_pronunciation

    assert wiki_pronunciation("エグいよ") == "에구이요"
    assert wiki_pronunciation("レイニーブーツ") == "레이니이 부우츠"
    # 진짜 장음(표층에 ー가 그대로 있는 것)은 그대로 남아야 한다 — 이 규칙이
    # 장음을 이중모음으로 바꿔 버리면 반대 방향 회귀가 된다
    assert wiki_pronunciation("ゲーム") == "게에무"
    assert wiki_pronunciation("コーヒー") == "코오히이"


def test_orphan_prefix_does_not_use_the_prefix_reading():
    """붙을 내용어가 없는 접두사는 접두사 읽기를 쓰면 안 된다.

    실측: 「さり気ない愛 盛りすぎる愛」의 첫 愛가 接頭辞/まな로 읽혀 「마나」가 됐다
    (정답 「아이」). 같은 줄 끝의 愛는 名詞/あい로 제대로 읽혔다 — 사전이 아니라 자리가
    문제이므로, 뒤가 공백·문장부호·줄끝이면 접두사 읽기를 버린다.
    """
    from everyric2.text.pron_style import wiki_pronunciation

    got = wiki_pronunciation("さり気ない愛 盛りすぎる愛")
    assert "마나" not in got, got
    assert got.count("아이") == 2, got


def test_katakana_particle_keeps_its_phonetic_reading():
    # 조사·조동사는 표기와 음가가 갈리는 유일한 부류라 표층 규칙에서 제외한다.
    # 가타카나로 적힌 조사까지 표층대로 읽으면 は→ワ 대립이 사라진다.
    from everyric2.text.ja_reading import _PARTICLE_POS

    assert "助詞" in _PARTICLE_POS and "助動詞" in _PARTICLE_POS


# ---------------------------------------------------------------------------
# 아라비아 숫자 + 조수사 읽기 — 실측 근거: tests/fixtures/wiki_pron_sample.json
# 115줄 중 아라비아 숫자 뒤에 조수사가 온 사례는 "1秒"(사람 발음 「이치뵤오」) 단 1건.
# 다른 조수사(分・回・人 등)는 표본에 없어 ``_MEASURED_ARABIC_COUNTERS``에 넣지 않았고,
# 아래 미적용 케이스가 그 경계를 지킨다.
# ---------------------------------------------------------------------------


def test_arabic_digit_before_a_measured_counter_reads_as_a_number():
    # 실측 그 자체(위키 사람 발음): 1秒 → 이치뵤오. UniDic은 아라비아 숫자에 읽기를
    # 주지 않아(feature.kana/pron 둘 다 빈값) 손대지 않으면 표면 "1"이 그대로 샌다.
    assert kana_reading("1秒") == "いちびょう"
    assert kana_reading("スケジュールは1秒先だって書き記していたい") == (
        "すけじゅーるはいちびょうせんだってかきしるしていたい"
    )


@pytest.mark.parametrize(
    ("digits", "expected_prefix"),
    [("0", "ゼロ"), ("3", "さん"), ("10", "じゅう"), ("24", "にじゅうよん")],
)
def test_arabic_digit_reading_scales_with_the_number(digits, expected_prefix):
    assert kana_reading(f"{digits}秒") == expected_prefix + "びょう"


def test_arabic_digit_before_an_unmeasured_counter_is_left_alone():
    # 分은 조수사에 따라 촉음화·반탁음화가 걸리는데(一分→いっぷん) 표본에 아라비아
    # 숫자+分 조합이 없다 — 잘못된 음변화를 새로 만드느니 기존 동작(숫자 그대로)을 지킨다.
    assert kana_reading("1分") == "1ふん"


def test_arabic_digit_needs_the_counter_immediately_adjacent():
    # 실측 형태("1秒", 사이 공백 없음)를 벗어나면 손대지 않는다.
    assert kana_reading("1 秒") == "1 びょう"


def test_standalone_arabic_digit_without_a_counter_is_unaffected():
    # 뒤에 조수사가 없으면(줄 끝, 다른 낱말) 숫자 읽기를 시도하지 않는다 — 실측 밖이다.
    assert kana_reading("2024年") == "2024ねん"
