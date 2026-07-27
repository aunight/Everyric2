"""가나 읽기 분해 + 발음 표기 DP 정렬 회귀 테스트 (everyric2.text.reading).

no-mock: pykakasi 실제 변환 결과를 그대로 사용한다. 발음 표기 예시는 보카로 위키에
실제로 쓰이는 표기 관습(로키 등)을 본떴다.
"""
import pytest

from everyric2.text.reading import (
    align_pron_to_moras,
    pron_segments_for_line,
    text_to_moras,
)


def test_text_to_moras_basic_arubaito():
    # 아루바이토(5) + 와(は, 1) + 네쿠라모오도(ー 장음 포함 6) = 12모라
    moras = text_to_moras("アルバイトはネクラモード")
    assert [m.kana for m in moras] == [
        "あ", "る", "ば", "い", "と", "は", "ね", "く", "ら", "も", "ー", "ど",
    ]
    assert len(moras) == 12
    # 장음 ー는 독립된 1모라
    assert moras[10].kana == "ー"
    assert all(not m.is_ascii for m in moras)


def test_text_to_moras_youon_combines_into_one_mora():
    # テレキャスター: きゃ(キ+ャ)가 결합해 1모라
    moras = text_to_moras("テレキャスター")
    kanas = [m.kana for m in moras]
    assert kanas == ["て", "れ", "きゃ", "す", "た", "ー"]
    assert len(moras) == 6


def test_text_to_moras_sokuon_gets_its_own_char_span():
    # 背負った: 정밀 귀속(2026-07-28) 후 — 한자 런(背負)이 せ·お를 공유하고, 오쿠리가나
    # 촉음 っ은 **자기 글자**(원문 2번째 위치)에 붙는다. 예전에는 토큰 전체(0,3)를
    # 공유해 순간이동 뭉침의 재료가 됐다.
    moras = text_to_moras("背負った")
    kanas = [m.kana for m in moras]
    assert kanas == ["せ", "お", "っ", "た"]
    assert (moras[0].char_start, moras[0].char_end) == (0, 2)
    assert (moras[1].char_start, moras[1].char_end) == (0, 2)
    assert (moras[2].char_start, moras[2].char_end) == (2, 3)
    # た는 별도 토큰(원문 3번째 글자)
    assert (moras[3].char_start, moras[3].char_end) == (3, 4)


def test_align_pron_to_moras_full_line_high_quality():
    # 아루바이토와 네쿠라 모오도 - は가 조사로서 "와"로 읽히는 가창 관습까지 포함해
    # 전 음절이 1:1로 resolved 되어야 하고 품질이 높아야 한다.
    moras = text_to_moras("アルバイトはネクラモード")
    syllables, quality = align_pron_to_moras(moras, "아루바이토와 네쿠라 모오도")

    assert quality >= 0.8
    assert len(syllables) == 12
    assert all(s.resolved for s in syllables)
    # 모라 구간은 순서대로 단조 증가하며 1:1 매칭이다
    assert [s.mora_start for s in syllables] == list(range(12))
    assert [s.mora_end for s in syllables] == [i + 1 for i in range(12)]
    # 촉음/장음 규칙: ー(모라 10)가 "오"에 매칭
    assert syllables[10].text == "오"
    assert syllables[10].mora_start == 10


def test_align_pron_to_moras_sokuon_absorbed_as_batchim():
    # ゆーて お坊っちゃんお嬢ちゃん ↔ 유우테 오봇짱 오죠오짱
    # 촉음(っ)·발음(ん)이 직전 음절 받침으로 흡수되어야 하며, 전체적으로 단조·전 커버.
    moras = text_to_moras("ゆーて お坊っちゃんお嬢ちゃん")
    syllables, quality = align_pron_to_moras(moras, "유우테 오봇짱 오죠오짱")

    assert quality >= 0.5
    assert all(s.resolved for s in syllables)
    # 단조 증가(역순 없음)
    starts = [s.mora_start for s in syllables]
    assert starts == sorted(starts)
    # 모든 모라가 어딘가에는 커버된다 (마지막 음절의 mora_end가 전체 모라 수와 일치)
    assert syllables[-1].mora_end == len(moras)
    # っ 뒤에 오는 봇 음절이 っ까지 흡수해 mora_end가 +1 확장되어야 한다
    bot = next(s for s in syllables if s.text == "봇")
    assert bot.mora_end - bot.mora_start == 2
    absorbed_kana = moras[bot.mora_end - 1].kana
    assert absorbed_kana == "っ"


def test_align_pron_to_moras_ascii_unit_allows_one_to_many():
    # Don't Stop！ ↔ 돈 스탑 - ASCII 유닛(Don't, Stop)에 여러 음절이 몰려 배정될 수
    # 있다(1:N). 정확히 어느 유닛에 몰리는지는 비용이 동률이라 결정론적이되 자명하지
    # 않으므로, 여기서는 1:N 패턴 자체와 resolved 유지만 검증한다.
    moras = text_to_moras("Don't Stop！")
    assert len(moras) == 2
    assert all(m.is_ascii for m in moras)

    syllables, quality = align_pron_to_moras(moras, "돈 스탑")

    assert len(syllables) == 3
    assert all(s.resolved for s in syllables)
    assert quality >= 0.5
    # 두 ASCII 모라가 모두 사용되고, 최소 하나는 2개 이상의 음절을 공유한다(1:N)
    ranges = [(s.mora_start, s.mora_end) for s in syllables]
    assert set(ranges) == {(0, 1), (1, 2)}
    assert any(ranges.count(r) >= 2 for r in set(ranges))
    # 순서는 항상 보존된다 (mora_start가 감소하지 않음)
    assert [r[0] for r in ranges] == sorted(r[0] for r in ranges)


def test_align_pron_to_moras_kanji_multi_mora():
    # 長い前髪(ながいまえがみ): 長い 토큰이 3모라(な,が,い)를 갖고 前髪 토큰이
    # 4모라(ま,え,が,み)를 갖는다. pron과 1:1 정렬되어야 한다.
    moras = text_to_moras("長い前髪")
    assert [m.kana for m in moras] == ["な", "が", "い", "ま", "え", "が", "み"]
    # 정밀 귀속: 長(한자)=なが, 오쿠리가나 い는 자기 글자. 前髪(전부 한자)은 통짜 유지.
    assert (moras[0].char_start, moras[0].char_end) == (0, 1)
    assert (moras[2].char_start, moras[2].char_end) == (1, 2)
    assert (moras[3].char_start, moras[3].char_end) == (2, 4)

    syllables, quality = align_pron_to_moras(moras, "나가이 마에가미")
    assert quality >= 0.8
    assert len(syllables) == 7
    assert all(s.resolved for s in syllables)
    assert [s.mora_start for s in syllables] == list(range(7))


def test_pron_segments_for_line_monotonic_and_span_preserving():
    # 등간격 합성 char_spans(0.1초/글자)로 음절 세그먼트가 단조 증가하고
    # 전체 구간(0.0~1.2초)을 보존하는지 확인한다.
    text = "アルバイトはネクラモード"
    pron = "아루바이토와 네쿠라 모오도"
    char_spans = [(ch, i * 0.1, (i + 1) * 0.1) for i, ch in enumerate(text)]

    segments = pron_segments_for_line(char_spans, text, pron)

    assert segments is not None
    assert len(segments) == 12
    assert segments[0]["start"] == pytest.approx(0.0)
    assert segments[-1]["end"] == pytest.approx(1.2)
    for prev, cur in zip(segments, segments[1:]):
        assert cur["start"] >= prev["end"] - 1e-9
        assert cur["end"] >= cur["start"]
    assert all(s["resolved"] for s in segments)


def test_pron_segments_for_line_interpolates_missing_char():
    # CTC가 일부 글자(OOV 드롭)를 건너뛰어도 이웃 글자 시간으로 보간되어 전체
    # 구간을 보존한 채 세그먼트가 나와야 한다.
    text = "アルバイトはネクラモード"
    pron = "아루바이토와 네쿠라 모오도"
    char_spans = [
        (ch, i * 0.1, (i + 1) * 0.1) for i, ch in enumerate(text) if ch != "は"
    ]

    segments = pron_segments_for_line(char_spans, text, pron)

    assert segments is not None
    assert len(segments) == 12
    assert segments[0]["start"] == pytest.approx(0.0)
    assert segments[-1]["end"] == pytest.approx(1.2)
    for prev, cur in zip(segments, segments[1:]):
        assert cur["start"] >= prev["end"] - 1e-9


def test_pron_segments_for_line_returns_none_for_bad_pronunciation():
    # 엉터리 발음(가나 행/모음과 무관한 음절 반복)은 품질 미달로 None을 반환해야
    # 호출부가 그라데이션 폴백을 쓸 수 있게 한다.
    text = "アルバイトはネクラモード"
    char_spans = [(ch, i * 0.1, (i + 1) * 0.1) for i, ch in enumerate(text)]
    bad_pron = "뻐" * len(text_to_moras(text))

    assert pron_segments_for_line(char_spans, text, bad_pron) is None


def test_pron_segments_for_line_empty_inputs_return_none():
    assert pron_segments_for_line([], "アルバイト", "아루바이토") is None
    assert pron_segments_for_line([("あ", 0.0, 0.1)], "", "아") is None


if __name__ == "__main__":
    # pytest 없이도 검증 가능한 러너 (Everyric2 venv는 런타임 전용)
    _fns = sorted(
        (v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)),
        key=lambda f: f.__code__.co_firstlineno,
    )
    for _fn in _fns:
        _fn()
        print(f"PASS {_fn.__name__}")
    print(f"\n{len(_fns)} passed")


# ---------------------------------------------------------------------------
# 모라 → 글자 정밀 귀속 (2026-07-28) — «순간이동» 사고의 뿌리 수정
# ---------------------------------------------------------------------------
# 한 토큰의 모든 모라가 토큰 전체 구간을 공유하면, 역매핑에서 그 글자들이 같은 스팬을
# 받고 단조 클램프가 첫 글자에 몰아준 뒤 나머지를 제로폭으로 만든다 — JW3N-HvU0MA의
# フラッシュバック(외래어 한 토큰) ラ~ク 7글자가 한 시각에 점등한 실측 사고. 가나는
# 표면과 읽기가 위치 대응하므로 세분하고, 한자 런은 가나 앵커 사이 읽기를 공유한다.


def test_moras_attribute_kana_tokens_per_char():
    moras = text_to_moras("フラッシュバック")
    assert [(m.kana, m.char_start, m.char_end) for m in moras] == [
        ("ふ", 0, 1), ("ら", 1, 2), ("っ", 2, 3), ("しゅ", 3, 5),
        ("ば", 5, 6), ("っ", 6, 7), ("く", 7, 8),
    ]


def test_moras_distribute_okurigana_like_furigana():
    # 愛し合える → 愛=あい / し / 合=あ / え / る (후리가나 분배와 동일)
    moras = text_to_moras("愛し合える")
    assert [(m.kana, m.char_start, m.char_end) for m in moras] == [
        ("あ", 0, 1), ("い", 0, 1), ("し", 1, 2), ("あ", 2, 3), ("え", 3, 4), ("る", 4, 5),
    ]


def test_moras_keep_long_vowel_marks_positional():
    moras = text_to_moras("スーパー")
    assert [(m.kana, m.char_start) for m in moras] == [
        ("す", 0), ("ー", 1), ("ぱ", 2), ("ー", 3),
    ]


def test_token_span_refinement_falls_back_on_mismatch():
    # 표면 가나와 읽기가 어긋나면(아테지류) 확신이 없다 — None을 돌려 통짜 구간을 지킨다
    from everyric2.text.reading import _token_mora_char_spans

    assert _token_mora_char_spans("は", 0, ["わ"]) is None
    assert _token_mora_char_spans("愛", 0, []) is None


# ---------------------------------------------------------------------------
# &(앤드) — pron_style.py가 라틴 낱말 "and"로 렌더하는 것과 짝을 맞춘 ASCII 유닛
# 인식(2026-07). &는 品詞상 부호(補助記号)라 일본어 갈래로도, 예전 정규식으로는 ASCII
# 갈래로도 못 갔다 — 모라가 아예 안 생겨 그 자리의 "앤"이 카라오케 타이밍 없이 통째로
# 빠졌다(실측: pron_segments_for_line 결과에 그 세그먼트가 없었다). _ASCII_WORD_RE에
# &/＆를 추가해 다른 ASCII 낱말과 같은 취급을 받게 한다.
# ---------------------------------------------------------------------------


def test_ampersand_gets_its_own_ascii_mora():
    moras = text_to_moras("君&僕")
    assert [(m.kana, m.char_start, m.char_end, m.is_ascii) for m in moras] == [
        ("き", 0, 1, False), ("み", 0, 1, False),
        ("&", 1, 2, True),
        ("ぼ", 2, 3, False), ("く", 2, 3, False),
    ]


def test_ampersand_fullwidth_gets_its_own_ascii_mora_too():
    moras = text_to_moras("君＆僕")
    assert [m.kana for m in moras] == ["き", "み", "＆", "ぼ", "く"]
    assert moras[2].is_ascii and (moras[2].char_start, moras[2].char_end) == (1, 2)


def test_ampersand_does_not_merge_into_a_neighbouring_ascii_run_across_a_space():
    # "Boy & Girl" — 공백으로 갈린 세 낱말은 별개 모라 3개다(하나로 뭉치지 않는다)
    moras = text_to_moras("Boy & Girl")
    assert [(m.kana, m.is_ascii) for m in moras] == [
        ("Boy", True), ("&", True), ("Girl", True),
    ]


def test_ampersand_syllable_keeps_its_karaoke_timing_instead_of_vanishing():
    """&가 렌더한 "앤" 음절이 pron_segments_for_line에서 사라지지 않는다.

    수정 전 재현: text_to_moras가 &에 모라를 안 만들어 DP 정렬에 "앤"이 붙을 자리가
    없었고, 그 세그먼트가 통째로 빠졌다(karaoke 하이라이트가 안 뜬다). ASCII 유닛으로
    인식되면 다른 라틴 낱말과 똑같이 시간을 받는다.
    """
    text = "君&僕"
    pron = "키미 앤 보쿠"
    char_spans = [(c, 1.0 + i * 0.2, 1.2 + i * 0.2) for i, c in enumerate("君&僕")]

    segments = pron_segments_for_line(char_spans, text, pron)

    assert segments is not None
    texts = [s["text"] for s in segments]
    assert "앤" in texts, f"& 음절이 통째로 빠졌다 — {texts}"
    ande = next(s for s in segments if s["text"] == "앤")
    assert ande["start"] < ande["end"]  # 실제 시간 구간을 받는다(제로폭이 아니다)
    # 단조 증가·전 커버 — 다른 회귀 테스트와 같은 기준
    for prev, cur in zip(segments, segments[1:]):
        assert cur["start"] >= prev["end"] - 1e-9


# ---------------------------------------------------------------------------
# 부작용 검증 — &가 인접 글자와 붙어 있으면(공백 없이) 하나의 ASCII 런으로 뭉친다.
#
# _ASCII_WORD_RE에 &/＆를 word 문자로 추가한 부작용이다: "R&B"는 예전엔 R(1모라) +
# B(1모라)였고 &는(예전 정규식으로도) 모라가 안 생겨 조용히 빠졌다. 지금은 "R&B"
# 전체가 ASCII 모라 1개(글자 런에 공백·비ASCII 경계가 없으면 뭉치는 기존 규칙,
# Don't Stop → 2모라 테스트와 같은 매커니즘)다. R·앤·B 세 음절이 이제 그 모라
# 하나에 다 붙는데, DP는 ASCII 모라에 여러 음절을 몰아 배정하는 것을 이미 저비용으로
# 허용한다(_syll_extra, test_align_pron_to_moras_ascii_unit_allows_one_to_many와 같은
# 축) — 그래서 품질은 유지된다. 다만 R·B 각각의 **낱글자 정밀 타이밍**은 잃는다(R&B
# 구간 전체를 음절 수만큼 균등 분할하는 값으로 근사된다) — & 이전에는 R·B가 각자
# 정밀 모라였으니 그만큼은 실제 트레이드오프다. 실측(아래): quality는 여전히 0.6
# 문턱을 크게 웃돌고(0.87~0.9) 세그먼트 개수가 음절 수와 정확히 맞아 하나도 안
# 빠진다 — Don't Stop류 기존 패턴보다 비율이 클 뿐 새로운 실패 유형은 아니다.
# ---------------------------------------------------------------------------


def test_ampersand_merges_into_an_adjacent_ascii_run_when_there_is_no_gap():
    # "R&B" — 공백 없이 붙어 있으면 R·&·B가 ASCII 모라 1개로 뭉친다(기존 ASCII 런
    # 규칙 그대로 적용된 것뿐 — &만의 특별 취급이 아니다)
    moras = text_to_moras("R&B")
    assert [(m.kana, m.char_start, m.char_end, m.is_ascii) for m in moras] == [
        ("R&B", 0, 3, True),
    ]

    moras2 = text_to_moras("AT&T")
    assert [(m.kana, m.is_ascii) for m in moras2] == [("AT&T", True)]


def test_ampersand_merged_run_still_aligns_at_high_quality():
    """R&B류(&가 다른 라틴 글자에 바로 붙은 줄)가 정렬 품질·타이밍 계약을 지킨다.

    _QUALITY_THRESHOLD(reading.py)는 0.6 — 그보다 한참 위(0.87 이상)를 못박는다.
    모든 음절이 세그먼트를 받는다(하나도 안 빠진다)는 것도 함께 확인한다 — 이게
    본질적으로 지켜야 하는 계약이고, R·B의 낱글자 정밀도 손실은 이 테스트가 다루는
    범위 밖이다(위 주석 참고, 알려진 트레이드오프).
    """
    from everyric2.text.pron_style import wiki_pronunciation

    for text in ("R&B", "これはR&Bです", "AT&T"):
        pron = wiki_pronunciation(text)
        moras = text_to_moras(text)
        syllables, quality = align_pron_to_moras(moras, pron)
        assert quality >= 0.85, (text, pron, quality)  # 0.6 문턱보다 한참 위
        assert len(syllables) == len([c for c in pron if not c.isspace()])
        assert all(s.resolved for s in syllables), (text, pron)

        char_spans = [
            (c, 1.0 + i * 0.15, 1.15 + i * 0.15)
            for i, c in enumerate(c for c in text if not c.isspace())
        ]
        segments = pron_segments_for_line(char_spans, text, pron)
        assert segments is not None
        assert len(segments) == len(syllables), (text, "세그먼트 일부가 빠졌다")
        assert all(s["start"] < s["end"] for s in segments)
