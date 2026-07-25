"""라틴 → 한글 조밀 음차(everyric2.text.latin_hangul) 회귀 테스트.

이 기능이 왜 있는지는 ``everyric2/text/latin_hangul.py`` 문서에 실측과 함께 적혀 있다.
여기서 못박는 것은 세 가지다.

1. **조밀형이 실제로 나온다** — ``tighten``이 실측에서 이긴 표기를 만들고, 규칙 엔진이
   표 없이도 실측 낱말을 재현한다.
2. **라틴이 없는 줄은 바뀌지 않는다** — 특히 일본어 독음의 어말 스·츠(します·デス)를
   건드리면 안 된다. 조밀화는 라틴 낱말 안에서만 일어난다.
3. **모라·역매핑 계약이 유지된다** — 라틴 줄의 발음이 한글로 바뀌어도 DP 정렬 품질이
   임계를 넘고 원문 글자 역매핑이 살아 있어야 한다. 죽으면 그 줄의 가라오케가 사라진다.

no-mock: fugashi + unidic-lite 실제 분석 결과를 그대로 쓴다.
"""
import pytest

from everyric2.text.latin_hangul import (
    _rules,
    latin_word_to_hangul,
    tighten,
    transliterate_latin,
)
from everyric2.text.pron_style import pronunciation_candidates, wiki_pronunciation
from everyric2.text.reading import (
    align_pron_to_moras,
    map_pron_alignment_to_line,
    pron_segments_for_line,
    text_to_moras,
)

# 파이프라인이 독음 정렬을 채택하는 품질 임계 (reading._QUALITY_THRESHOLD와 같은 값)
_QUALITY_THRESHOLD = 0.6

# 실측에 나온 라틴 줄들 (referee_truth.json / XKZIQlqVjjk·H7PR6K7xff0·ba7YbGO2aq4)
_LATIN_LINES = [
    "セオリー通りになんない it's alright",
    "ましてや all I need",
    "大変に今推し so good ラブ＆ピース",
    "欲してるんだって I'll take it, take it!",
    "温めますか？ お願いします yeah yeah",
    "L-O-P-P-I'm ハッピー Loppi Loppi",
    "当然 all I want",
    "焦がれた場所 so fine 嘘はない",
    "心を操るのはNG!",
    "ステージ果てまで なぜだコイツ信じらんねw",
    "ひらひら numb numb",
    "Approved Approved Approved Approved",
    "numb な僕",
]


# ---------------------------------------------------------------------------
# 1. 조밀화 — 실측에서 이긴 표기가 나오는가
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("conventional", "tight"),
    [
        # 같은 emission·같은 창에서 관습형과 조밀형을 직접 채점한 값 (nats/token, 클수록 좋다)
        ("어프루브드", "어프룹"),  # -0.848 → -0.562 (원문 Approved는 -1.254)
        ("리보크", "리복"),  # -1.383 → -0.743 (원문 Revoke는 -3.083)
        ("니드", "닏"),  # -0.434 → -0.306
        ("원트", "원"),  # -0.671 → -0.522
        ("테이크", "테익"),  # -0.877 → -0.805
        ("위드", "윋"),  # -1.128 → -1.047
    ],
)
def test_tighten_reproduces_the_measured_winners(conventional, tight):
    """관습 음차 → 조밀 음차. 이 6쌍이 조밀형을 택한 근거 그 자체다."""
    assert tighten(conventional) == tight


def test_tighten_drops_a_stop_that_has_nowhere_to_go():
    # 앞 음절에 이미 받침이 있으면 그 파열음은 노래에 없다 — 원트의 t, 어프루브드의 d
    assert tighten("원트") == "원"
    assert tighten("월드") == "월"


def test_tighten_keeps_a_sibilant_that_has_nowhere_to_go():
    # 치찰음은 홀로도 들린다 — 실측의 it's가 잇이 아니라 잇츠(2음절)다.
    # 텍스트는 트(파열음)만 버리고 스는 남는다.
    assert tighten("텍스트") == "텍스"
    assert tighten("킵스") == "킵스"
    assert tighten("메익스") == "메익스"


def test_tighten_leaves_the_middle_of_a_word_alone():
    # 낱말 가운데의 ㅡ 음절은 한국어 화자가 실제로 부른다 — 어말만 손댄다
    assert tighten("스트롱") == "스트롱"
    assert tighten("블루") == "블루"
    assert tighten("드립") == "드립"


def test_tighten_is_idempotent():
    for word in ("어프루브드", "니드", "원트", "테이크", "블루", "커버"):
        once = tighten(word)
        assert tighten(once) == once


def test_tighten_ignores_syllables_it_cannot_read():
    # 한글이 아닌 글자가 섞이면 판단 근거가 없다 — 그대로 둔다
    assert tighten("abc") == "abc"
    assert tighten("") == ""
    assert tighten("드") == "드"  # 한 음절뿐이면 접을 앞 음절이 없다


# ---------------------------------------------------------------------------
# 2. 규칙 엔진 — 표 없이도 실측 낱말이 나오는가
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        # 아래는 모두 **표에 없는** 낱말이다. 규칙이 실측값을 그대로 만들어야 한다 —
        # 표에 넣어 버리면 규칙이 나중에 망가져도 표가 가려서 모른다.
        ("need", "닏"),
        ("take", "테익"),
        ("fine", "파인"),
        ("keep", "킵"),
        ("drip", "드립"),
        ("blue", "블루"),  # 자음 + l은 앞 음절에 ㄹ을 남긴다
        ("all", "올"),  # 어두운 l 앞의 a는 낮아진다
        ("it", "잇"),
        ("in", "인"),
        ("hey", "헤이"),
        ("loppi", "로피"),
        ("so", "소"),
        ("numb", "넘"),  # 어말 mb의 b는 묵음
    ],
)
def test_rules_alone_reproduce_the_measured_words(word, expected):
    assert tighten(_rules(word)) == expected
    assert latin_word_to_hangul(word) == expected


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        # 같은 규칙이 만드는 다른 낱말들 — 음절 수가 가창과 맞는지가 판정 기준이다
        ("night", "나잇"),
        ("light", "라잇"),
        ("life", "라입"),
        ("my", "마이"),  # 단음절의 어말 y는 /aɪ/
        # 다음절의 어말 y는 /i/. 열린 음절의 장모음화(baby /beɪ/)는 규칙으로 넣지 않았다 —
        # VCV가 길어지는지(baby·later)는 짧게 남는지(money·very·city) 영어 정서법이 반반이라
        # 어느 쪽으로 정해도 절반이 틀린다. 배비는 관습 표기(베이비)와 다르지만 가창 음절
        # 수(2)와는 오히려 맞는다 — 우리가 최적화하는 축이 그쪽이다.
        ("baby", "배비"),
        ("why", "와이"),  # wh는 활음 w
        ("what", "왓"),
        ("hello", "헬로"),  # 모음 사이의 l이 ㄹ 받침을 남긴다
        ("stop", "스톱"),
        ("quick", "퀵"),  # qu는 활음을 초성과 한 음절에 넣는다
        ("snow", "스노"),  # 어말 ow는 /oʊ/ (스나우가 아니다)
        ("honey", "호니"),  # 다음절의 어말 ey는 /i/ — hey는 헤이로 남는다
        ("catch", "캐치"),  # tch는 한 자소 (캣치가 되면 없는 음절이 생긴다)
        ("mother", "모더"),  # 모음 사이의 th는 유성 (모서가 아니다)
        ("fire", "파이"),  # 후치 r은 모음에 흡수된다 (파일이 아니다)
        ("angel", "앤겔"),  # 뒤에 모음이 오는 ng는 /ŋ/이 아니다 (애엘이 되면 자음이 사라진다)
        ("believe", "벨립"),  # 어말이 아닌 ie는 /iː/
        # ``ear`` — ``ea``가 먼저 먹으면 모음이 틀린다(learn→린). 자음이 뒤따르면 /ɜːr/,
        # 어말이면 /ɪər/. ``ar``은 원래부터 맞았다(car 카, part 팟) — 사상 문제가 아니라
        # 자소 우선순위 문제였다.
        ("learn", "런"),
        ("earth", "엇"),
        ("heard", "헏"),
        ("search", "서치"),
        ("early", "얼리"),
        ("hear", "히"),
        ("year", "이"),
        ("years", "잇"),  # 복수의 s는 어말 판정을 막지 않는다
        ("car", "카"),
        ("part", "팟"),
        ("start", "스탓"),
    ],
)
def test_rules_follow_korean_loanword_shapes(word, expected):
    assert latin_word_to_hangul(word) == expected


def test_unknown_words_always_come_out_as_hangul():
    # 규칙이 정확할 필요는 없다 — 정렬기가 대응시킬 수 있는 한글이 나오고 음절 수가
    # 대략 맞으면 된다는 것이 실측의 결론이다. 라틴이 남으면 그 줄은 정렬되지 않는다.
    for word in ("shimmer", "gravity", "wonderland", "silhouette", "crescendo"):
        got = latin_word_to_hangul(word)
        assert got and not any(ch.isascii() and ch.isalpha() for ch in got), got


# ---------------------------------------------------------------------------
# 3. 표 · 글자 이름 · 넷 슬랭
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("approved", "어프룹"),
        ("revoke", "리복"),
        ("with", "윋"),
        ("want", "원"),
        ("good", "굿"),
        ("it's", "잇츠"),  # 조밀 규칙을 한 번 더 걸면 잇이 된다 — 표 값은 마감된 값이다
        ("I'm", "아임"),
        ("I'll", "아일"),
        ("yeah", "예"),
        ("OK", "오케이"),
        ("cover", "커버"),
        ("alright", "올라잇"),
        # give는 규칙으로도 나왔었지만(-ive를 magic e에서 뺐을 때) 그 예외가 drive·five·
        # alive를 망가뜨려 걷어냈다. 그래서 실측값을 표에서 잡는다.
        ("give", "깁"),
        # heart·hearth만 ear를 /ɑːr/로 읽는다(닫힌 예외 2개) — 규칙은 /ɜːr/(런·엇)로 간다
        ("heart", "핫"),
        # 일본어 가사의 라틴이 영어가 아니라 가타카나로 불리는 부류. ボーカロイド는 6음절이고
        # 규칙의 영어 읽기(보캘로읻)는 4음절이다. 코퍼스에서 33회/4곡으로 가장 흔하다.
        ("VOCALOID", "보오카로이도"),
    ],
)
def test_pinned_words_are_used_verbatim(word, expected):
    assert latin_word_to_hangul(word) == expected


@pytest.mark.parametrize(
    ("word", "expected"),
    [("drive", "드라입"), ("five", "파입"), ("alive", "앨라입"), ("die", "다이")],
)
def test_magic_e_is_not_blocked_for_ive(word, expected):
    # give·live만 짧다 — 그 둘을 규칙으로 잡으려다 이쪽을 다 잃는 것이 손해가 크다
    assert latin_word_to_hangul(word) == expected


def test_single_letters_and_vowelless_initialisms_are_spelled_out():
    # 실측: H7PR6K7xff0의 L-O-P-P-I'm이 사람 자막에서 「엘-오-피-피-아임」, NG!가 「엔지이」다
    assert [latin_word_to_hangul(c) for c in "LOPPI"] == ["엘", "오", "피", "피", "아이"]
    assert latin_word_to_hangul("NG") == "엔지"
    assert latin_word_to_hangul("TV") == "티브이"
    # 관사 a는 글자 이름(에이)이 아니다 — 부르지 않는 음절이 하나 늘어난다
    assert latin_word_to_hangul("a") == "어"


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        # 모음이 있어 자모 구성으로는 낱말과 구별되지 않는다 — 표로 잡는다.
        # ATM을 낱말로 읽으면 앳(1음절)인데 사람은 에이티엠(4음절)을 부른다. 이 변경이
        # 고치려던 오류(음절 수 불일치)를 반대 방향으로 만드는 것이라 반드시 잡아야 한다.
        ("ATM", "에이티엠"),
        ("VIP", "브이아이피"),
        ("ID", "아이디"),
        ("USB", "유에스비"),
        ("DVD", "디브이디"),
        # 모음이 없으면 표 없이도 잡힌다
        ("NG", "엔지"),
        ("BGM", "비지엠"),
        ("SNS", "에스엔에스"),
    ],
)
def test_acronyms_are_spelled_out(word, expected):
    assert latin_word_to_hangul(word) == expected


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        # **대문자 낱말이 깨지지 않는다.** 이 방향이 더 중요하다: 로컬 코퍼스 4662줄에서
        # 전부 대문자 토큰은 낱말이 압도적이다(VOCALOID 33회/4곡 · BOY 4회 · VOX 2회 대
        # NG 2회 · AC 2회). BOY(3자)·AT(2자)가 "길이로 두문자어를 가른다"의 직접 반례다.
        ("BOY", "보이"),
        ("VOX", "복스"),
        ("LOVE", "럽"),
        ("STOP", "스톱"),
        ("YEAH", "예"),
        ("AIM", "에임"),  # 팀리드 보고의 'VIP 名声 AIM' — AIM은 낱말이다
        ("HELLO", "헬로"),
        ("DANCE", "댄스"),
        ("YES", "예스"),
        ("HEY", "헤이"),
    ],
)
def test_uppercase_words_are_not_spelled_out(word, expected):
    assert latin_word_to_hangul(word) == expected


def test_the_acronym_rule_needs_uppercase_source():
    # 소문자는 낱말이다 — 두문자어 목록에 있어도 적용하지 않는다
    assert latin_word_to_hangul("id") != "아이디"
    assert latin_word_to_hangul("atm") != "에이티엠"


def test_acronym_lines_from_the_defect_report():
    assert wiki_pronunciation("おまえはATM") == "오마에와 에이티엠"
    assert wiki_pronunciation("VIP 名声 AIM") == "브이아이피 메이세이 에임"


def test_bare_w_is_dropped_because_it_is_laughter_not_a_word():
    # 사람 자막은 「なぜだコイツ信じらんねw」를 「…신지란네」로만 적는다(referee_truth 2줄).
    # 더블유를 넣으면 없는 음절 3개가 생긴다. 계측기에서도 이 2줄이 독음오류로 세어졌다.
    assert latin_word_to_hangul("w") == ""
    assert latin_word_to_hangul("www") == ""
    assert wiki_pronunciation("なぜだコイツ信じらんねw") == "나제다 코이츠 신지란네"
    # 대문자 W가 약어 안에 있으면 글자 이름으로 읽는다
    assert latin_word_to_hangul("BMW") == "비엠더블유"


def test_the_tokenizer_split_apostrophe_is_rejoined():
    # 형태소 분석기가 it's를 it / ' / s로 쪼개고 문절 렌더가 그 사이를 띄운다("it' s").
    # 낱말 단위 조회가 그 공백을 넘어야 표가 맞는다.
    assert transliterate_latin("it' s") == "잇츠"
    assert transliterate_latin("I' ll") == "아일"
    assert wiki_pronunciation("セオリー通りになんない it's alright") == (
        "세오리이 토오리니 난나이 잇츠 올라잇"
    )


# ---------------------------------------------------------------------------
# 4. 라틴이 없는 줄은 바뀌지 않는다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pron",
    [
        "이키마스 데스 시마스",  # 어말 스는 す에서 온 진짜 모라다 — 시맛이 되면 곡이 망가진다
        "코이츠 신지란네",  # 츠도 마찬가지 (つ)
        "사켄다 오토와 스데니 레츠오 나사나이데",
        "카타스토로휘이",
        "다! 요! 네! 추!",
        "「킷토 다레니모 와카라나이사」",
        "1234 (56)",
        "",
    ],
)
def test_strings_without_latin_pass_through_untouched(pron):
    assert transliterate_latin(pron) == pron


def test_japanese_lines_are_byte_identical_to_the_pre_transliteration_render():
    # 조밀화가 일본어 독음으로 새면 여기서 잡힌다 (します → 시맛 등)
    assert wiki_pronunciation("行きます") == "이키마스"
    assert wiki_pronunciation("ここで君を待っている") == "코코데 키미오 맛테이루"
    assert wiki_pronunciation("叫んだ音は既に列を成さないで") == (
        "사켄다 오토와 스데니 레츠오 나사나이데"
    )


def test_mm_is_a_unit_after_a_digit_and_an_interjection_otherwise():
    # 사람 자막: 「0.1mmの距離」 → 「레이텐 이치미리노 쿄리」(ミリ), 「ひらひら mm mm」 → 「음 음」.
    # 숫자 읽기 자체는 손대지 않으므로 앞의 0.1은 그대로 남는다(아래 테스트가 그 미해결을 기록).
    assert transliterate_latin("0. 1mm노") == "0. 1미리노"
    assert transliterate_latin("히라히라 mm mm") == "히라히라 음 음"


def test_digits_are_not_transliterated():
    # 사람은 「1秒」를 「이치뵤오」로 읽지만 숫자 읽기는 조수사·문맥에 달린 별개 문제다.
    # 이 테스트는 그 미해결을 기록해 둔다 — 흉내면 틀린 값을 박게 된다.
    assert "1" in wiki_pronunciation("1秒先")
    assert transliterate_latin("2024") == "2024"


# ---------------------------------------------------------------------------
# 5. 모라·역매핑 계약
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", _LATIN_LINES)
def test_latin_lines_keep_the_mora_alignment_contract(text):
    """한글로 바뀐 발음이 모라 정렬·역매핑을 통과해야 한다.

    ``text_to_moras``는 라틴 낱말 하나를 ASCII 모라 1개로 만들고, DP는 그 모라에 음절을
    여러 개 몰아 배정할 수 있다(``_syll_extra``, 저비용). 그래서 음차로 음절 수가 늘어도
    정렬이 깨지지 않는다 — 이 테스트가 그 계약을 못박는다. 깨지면 품질 임계(0.6) 미만이
    되어 그 줄의 원문 글자 타이밍이 통째로 사라진다.
    """
    pron = wiki_pronunciation(text)
    assert pron
    syllables = [c for c in pron if not c.isspace()]
    moras = text_to_moras(text)
    assert moras

    result, quality = align_pron_to_moras(moras, pron)
    assert quality >= _QUALITY_THRESHOLD, (text, pron, quality)
    # 역매핑은 DP 결과와 음절 인덱스가 1:1이어야 성립한다 (_map_syllable_times_to_chars)
    assert len(result) == len(syllables)

    spans = [(c, 1.0 + i * 0.2, 1.2 + i * 0.2, 0.5) for i, c in enumerate(syllables)]
    words, pron_segments = map_pron_alignment_to_line(text, pron, spans)
    assert words is not None, "원문 글자 역매핑이 죽으면 라인 타이밍만 남는다"
    assert pron_segments and len(pron_segments) == len(syllables)
    assert "".join(w["word"] for w in words) == text.replace(" ", "")


@pytest.mark.parametrize("text", _LATIN_LINES)
def test_latin_lines_still_produce_forward_pron_segments(text):
    # 정방향(원문 글자 CTC → 발음 음절 스팬)도 살아 있어야 한다. 이것이 라틴 줄에서
    # 가라오케 음절 채움을 가능하게 하는 값이다(예전에는 그라데이션 폴백만 됐다).
    pron = wiki_pronunciation(text)
    body = [c for c in text if not c.isspace()]
    char_spans = [(c, 1.0 + i * 0.2, 1.2 + i * 0.2) for i, c in enumerate(body)]
    segments = pron_segments_for_line(char_spans, text, pron)
    assert segments, (text, pron)
    got = [s["text"] for s in segments]
    # 부호는 매칭될 모라가 없으면 미해결로 빠질 수 있다(라인마다 다르다). 못박는 것은
    # 순서가 보존된 부분열이라는 것과, **한글 음절은 하나도 빠지지 않는다**는 것이다 —
    # 빠지면 그 음절만 채움 없이 지나간다.
    cursor = 0
    for ch in (c for c in pron if not c.isspace()):
        if cursor < len(got) and got[cursor] == ch:
            cursor += 1
    assert cursor == len(got), (got, pron)
    assert [c for c in got if "가" <= c <= "힣"] == [c for c in pron if "가" <= c <= "힣"]
    assert all(s["end"] >= s["start"] for s in segments)


def test_candidates_share_the_latin_transliteration_with_the_default():
    # 후보와 기본값이 라틴 표기에서 갈라지면 오디오 심판이 "독음 차이"가 아니라
    # "라틴 표기 차이"를 재게 된다 — 둘 다 _render_pronunciation을 지나야 한다.
    text = "当然 all I want"
    cands = pronunciation_candidates(text)
    assert cands and cands[0] == wiki_pronunciation(text)
    assert all("all" not in c and "want" not in c for c in cands)


def test_latin_only_lines_get_a_pronunciation_but_no_referee_candidates():
    # 라틴 100% 줄이 정렬이 가장 나쁜 줄이다(라틴 글자 conf<0.01이 90~99%) — 발음이 비면
    # 그 줄은 독음(ko) 정렬에 아예 들어가지 못한다. 반면 후보는 한자·조사 독음의 갈림을
    # 재는 축이라 라틴만 있는 줄에는 줄 것이 없다(후보 0개 → 심판 비용 0).
    assert wiki_pronunciation("Approved Approved") == "어프룹 어프룹"
    assert pronunciation_candidates("Approved Approved") == []
