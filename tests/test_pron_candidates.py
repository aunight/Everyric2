"""후보 독음 생성기(pron_style.pronunciation_candidates) + 워커 배선 테스트.

no-mock: fugashi + unidic-lite / pykakasi 실제 분석 결과를 그대로 쓴다. 여기서 고정하는
것은 "오디오 심판이 고를 수 있는 서로 다른 독음이 실제로 생성되는가"다 — 어느 후보가 맞는지는
오디오가 고르므로(tests/test_pron_referee.py) 이 파일은 정답을 주장하지 않는다.

**후보는 ``pron_style._AMBIGUOUS_WORDS``/``_STEM_AMBIGUOUS_WORDS`` 표에 있는 낱말로만
제한된다.** 예전엔 nbest·pykakasi·루비 미채택·표층 읽기(phonetic=False)까지 전부 후보
축으로 썼는데, 실오디오 검증(``s5Rkv_5Sbbo``, 134줄)에서 그 구현은 53줄을 갈아치웠고
53줄 전부 틀렸다. 원인은 심판이 아니라 후보 생성이었다 — 삭제가 섞이고(11건), kor
어댑터가 못 듣는 장음 표기 변종이 섞였다(21건). 후보를 낱말 전체 문자열 일치로
제한하고 오프라인 후처리 필터로 재측정하니 8건 맞고 0건 틀렸다.

그런데 그 필터를 후보 **생성** 단계로 그대로 옮기면(낱말 전체 일치) 弾く/行く가 사전형
(활용 안 된 꼴)일 때만 걸려 실오디오 재채점에서 5줄에서만 후보가 생겼다(맞게 2). 활용형
(弾いて, 行けば 등)은 후보가 아예 안 생겨서였다. 그래서 弾く/行く 두 항목만 "한자 어간 +
읽기 접두" 짝(``_STEM_AMBIGUOUS_WORDS``)으로 바꿔 활용 어미를 그대로 두고 어간 읽기만
바꾸게 했다 — 재측정하면 7줄에서 후보가 생기고(맞게 3, 틀리게 0), 나머지 격차는 후보가
안 생겨서가 아니라 그 순간의 오디오 점수가 마진을 못 넘어서였다(candidate 생성 문제가
아니라 채점 문제 — 이 파일이 검증하는 범위 밖이다).

이 파일의 대부분은 그 제한이 실제로 지켜지는지(표 밖 낱말은 후보를 안 만든다, 대립
읽기 외의 차이가 안 생긴다, 삭제가 없다, 활용형이 잡히되 品詞 다른 동음이의 한자
이웃(弾む·弾丸·糾弾·行方·行う 등)은 안 걸린다)를 못박는다.
"""
import re
from difflib import SequenceMatcher

import pytest

from everyric2.config.settings import AlignmentSettings
from everyric2.server.worker import _referee_candidates
from everyric2.text.pron_style import (
    _AMBIGUOUS_WORDS,
    _STEM_AMBIGUOUS_WORDS,
    pronunciation_candidates,
    wiki_pronunciation,
)


class _Line:
    def __init__(self, text):
        self.text = text


# ---------------------------------------------------------------------------
# 후보 목록의 계약
# ---------------------------------------------------------------------------


def test_first_candidate_is_exactly_the_deterministic_default():
    # [0]이 기본값이어야 심판이 "기본값을 마진 이상 이기는가"를 물을 수 있다
    for text in ("私は歩く", "三日月の夜", "何も言えない", "涙（シル）をこぼす"):
        assert pronunciation_candidates(text)[0] == wiki_pronunciation(text)


def test_candidates_are_distinct_and_non_empty():
    for text in ("彼はギターを弾く", "祭りの最中に", "今更止められない"):
        cands = pronunciation_candidates(text)
        assert len(cands) == len(set(cands)), cands
        assert all(c.strip() for c in cands)


def test_no_candidates_for_non_japanese_lines():
    # 심판을 돌릴 이유가 없는 라인은 빈 목록 → 비용 0
    for text in ("hello world", "오늘 밤", "", "123"):
        assert pronunciation_candidates(text) == []


def test_max_candidates_is_respected():
    # 弾く와 刃가 두 번씩 걸려 대안이 4개(+ 기본값 = 5개) 나오는 줄로 상한을 실제로 시험한다.
    text = "刃を弾く 刃を弾く"
    assert len(pronunciation_candidates(text)) == 5
    assert len(pronunciation_candidates(text, max_candidates=3)) == 3
    assert pronunciation_candidates(text, max_candidates=0) == []


# ---------------------------------------------------------------------------
# 애매 어휘 표 — 표에 있는 낱말만 후보를 만든다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,alternative",
    [
        ("彼はギターを弾く", "카레와 기타아오 하지쿠"),  # 튕기다 / 연주하다(기본값)
        ("一人で行く", "히토리데 유쿠"),  # 구어체(기본값) / 문어체
        ("祭りの最中に", "마츠리노 사나카니"),  # さいちゅう(기본값) / さなか
        ("好き好きな気持ち", "스키스키나 키모치"),  # 連濁(기본값) / 連濁 없음
        ("真に受ける", "마코토니 우케루"),  # しんに(기본값) / まことに
        ("何も言えない", "나니모 이에나이"),  # なんも(기본값) / なにも
        ("刃を研ぐ", "야이바오 토구"),  # は(기본값) / やいば
        ("この期に及んで", "코노고니 오욘데"),  # このき(기본값) / このご
    ],
)
def test_ambiguous_table_words_appear_as_candidates(text, alternative):
    cands = pronunciation_candidates(text)
    assert alternative in cands, f"{text}: {alternative} 가 후보에 없다 — {cands}"
    assert cands[0] != alternative, "표의 기본 읽기와 대안이 뒤바뀌면 안 된다"


@pytest.mark.parametrize(
    "text",
    [
        "私は歩く",  # 私(와타시/와타쿠시)는 표에 없다 — 옛 구현의 nbest 축이었다
        "三日月の夜",  # 三日月(미카즈키/밋카츠키)도 표에 없다
        "数え事をする",  # 連濁(카조에 고토/코토)도 표에 없다
        "涙（シル）をこぼす",  # 루비 미채택 축도 더는 없다
        "一緒に歩こう",  # 옛 구현에서 오오/오우 표기 변종이 나오던 낱말
        "この対象を見る",  # 위와 같은 부류(타이쇼오)
    ],
)
def test_words_outside_the_table_never_produce_alternatives(text):
    # 표 밖 낱말은 사전/문맥으로 못 가르는 축이 아니므로(또는 텍스트만으로 결정되므로)
    # 후보를 만들 이유가 없다 — 기본값 하나만 남아야 referee가 아예 안 돈다.
    cands = pronunciation_candidates(text)
    assert cands == [wiki_pronunciation(text)]


def test_ambiguous_table_has_no_degenerate_entries():
    # 표 항목이 스스로 규칙을 어기면(빈 읽기, 두 읽기가 같음) 실측이 무의미해진다.
    assert _AMBIGUOUS_WORDS
    for word, (reading_a, reading_b) in _AMBIGUOUS_WORDS.items():
        assert word, "낱말이 비어 있으면 절대 안 걸린다"
        assert reading_a and reading_b, f"{word}: 빈 읽기는 삭제 후보와 같다"
        assert reading_a != reading_b, f"{word}: 두 읽기가 같으면 후보가 기본값과 중복된다"
        assert re.fullmatch(r"[ぁ-ゖー]+", reading_a), f"{word}: {reading_a} — 히라가나가 아니다"
        assert re.fullmatch(r"[ぁ-ゖー]+", reading_b), f"{word}: {reading_b} — 히라가나가 아니다"


def test_stem_table_has_no_degenerate_entries():
    assert _STEM_AMBIGUOUS_WORDS
    for kanji, (prefix_a, prefix_b, allow_sokuon) in _STEM_AMBIGUOUS_WORDS.items():
        assert kanji, "한자 어간이 비어 있으면 절대 안 걸린다"
        assert prefix_a and prefix_b, f"{kanji}: 빈 읽기 접두는 삭제 후보와 같다"
        assert prefix_a != prefix_b, f"{kanji}: 두 접두가 같으면 후보가 기본값과 중복된다"
        assert re.fullmatch(r"[ぁ-ゖー]+", prefix_a), f"{kanji}: {prefix_a} — 히라가나가 아니다"
        assert re.fullmatch(r"[ぁ-ゖー]+", prefix_b), f"{kanji}: {prefix_b} — 히라가나가 아니다"
        assert isinstance(allow_sokuon, bool), f"{kanji}: 촉음 허용 플래그가 bool이 아니다"


# ---------------------------------------------------------------------------
# 활용 동사(_STEM_AMBIGUOUS_WORDS) — 어간 읽기만 바뀌고 활용 어미는 그대로여야 한다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,alternative",
    [
        # 弾く: 사전형 히쿠(기본값)의 활용형들 — 어미(테/타/코오/케레바/키마스)는 그대로,
        # 어간(히→하지)만 바뀐다
        ("あなたの言葉を弾いてみせるよ", "아나타노 코토바오 하지이테미세루요"),
        ("風を切って弾いた", "카제오 킷테 하지이타"),
        ("この手で弾こう", "코노 테데 하지코우"),
        # 行く: 이쿠(기본값)의 활용형들
        ("ああ、どこへ行けばいいの", "아아, 도코에 유케바 이이노"),
        ("行こうと思う", "유코우토 오모우"),
    ],
)
def test_stem_table_covers_inflected_verb_forms(text, alternative):
    # 이게 이번 확장의 핵심이다 — 낱말 전체 문자열 일치 방식은 사전형(弾く, 行く)만
    # 잡고 이 활용형들을 전부 놓쳤다(실오디오 재채점으로 확인).
    cands = pronunciation_candidates(text)
    assert alternative in cands, f"{text}: {alternative} 가 후보에 없다 — {cands}"
    assert cands[0] == wiki_pronunciation(text)


@pytest.mark.parametrize(
    "text",
    [
        "弾き語りをする",  # 名詞(히키가타리) — 읽기 접두만으론 안 걸러진다(품사 조건 필요)
        "弾む心",  # 별개 낱말(하즈무) — 읽기 접두가 안 맞음
        "弾丸が飛ぶ",  # 별개 낱말(단간)
        "糾弾する",  # 弾이 낱말 중간(큐우단) — 표층이 弾로 시작 안 함
        "行方不明になる",  # 名詞(유쿠에) — 읽기 접두만으론 안 걸러진다(품사 조건 필요)
        "テストを行った",  # 별개 낱말 行う의 활용(오코낫타) — 읽기 접두가 안 맞음
        "銀行の前で",  # 行이 낱말 중간(긴코오)
        "行動を起こす",  # 別語(코오도오) — 읽기 접두가 い/ゆ가 아니라서 안 걸림
        "行事に参加する",  # 別語(교오지) — 위와 같음
    ],
)
def test_stem_table_does_not_match_unrelated_kanji_neighbors(text):
    # 접두 매칭이라 오탐 위험이 크다 — 弾き語り/行方는 읽기 접두(히/유)까지 우연히
    # 맞아서 品詞=動詞 조건이 없으면 뚫린다(실측으로 찾은 함정). 나머지는 읽기 접두
    # 자체가 달라서 걸러진다.
    cands = pronunciation_candidates(text)
    assert cands == [wiki_pronunciation(text)], cands


@pytest.mark.parametrize(
    "text",
    [
        "行ったり来たりして",  # 行った의 촉음편(いった)은 いく 전용 — ゆった는 존재하지 않는다
        "行った",
        "テストに行って来た",
    ],
)
def test_stem_table_excludes_sokuon_inflection_for_iku(text):
    # 실측(재채점): 行ったり来たりして에서 만든 「윳타리」 후보가 오디오에 거부되긴 했지만
    # (gain -0.0564) 애초에 문법적으로 존재하지 않는 읽기였다 — 다른 곡에서는 이길 수
    # 있다. 促音便(っ으로 시작하는 활용 어미)은 行 항목에서 아예 후보를 안 만든다.
    cands = pronunciation_candidates(text)
    assert cands == [wiki_pronunciation(text)], cands


@pytest.mark.parametrize(
    "text",
    ["行けば", "行けない", "行こう", "行きたい", "行かない"],
)
def test_stem_table_still_covers_non_sokuon_iku_inflections(text):
    # 促音便 제외가 い/ゆ 축 자체를 없애면 안 된다 — 촉음이 아닌 활용형은 여전히 잡혀야 한다.
    cands = pronunciation_candidates(text)
    assert len(cands) > 1, f"{text}: 후보가 안 생겼다 — {cands}"


def test_naniga_is_in_the_table_but_nante_node_nanika_are_not():
    # 何が(나니가/난가)는 何も와 같은 なに/なん 축이라 표에 넣었다(실측: 사람 나니가 /
    # 기본값 난가). 何て・何で・何か는 표기가 고정이라 넣지 않는다 — 何를 어간으로 잡아
    # 넓히면 이 고정 낱말들까지 오탐이 된다.
    ga_cands = pronunciation_candidates("そこから何が見えるの？")
    assert "소코카라 나니가 미에루노?" in ga_cands

    for text in ("何てことだ", "何でだろう", "何か違う"):
        cands = pronunciation_candidates(text)
        assert cands == [wiki_pronunciation(text)], f"{text}: {cands} — 고정 표기인데 후보가 생겼다"


# ---------------------------------------------------------------------------
# 제한 규칙 1: 삭제 후보를 절대 만들지 않는다
# ---------------------------------------------------------------------------

_TABLE_EXAMPLES = {
    "弾く": "彼はギターを弾く",
    "行く": "一人で行く",
    "最中": "祭りの最中に",
    "好き好き": "好き好きな気持ち",
    "真に": "真に受ける",
    "何も": "何も言えない",
    "刃": "刃を研ぐ",
    "この期": "この期に及んで",
    # 활용 동사(_STEM_AMBIGUOUS_WORDS)의 활용형도 같은 규칙(삭제 없음, 그 낱말만 변경)을
    # 지켜야 한다 — 어간+접두 매칭이라 사전형과 다른 경로를 타므로 따로 넣는다.
    "弾く(활용)": "あなたの言葉を弾いてみせるよ",
    "行く(활용)": "ああ、どこへ行けばいいの",
}


def test_no_word_disappears_between_default_and_a_candidate():
    # 낱말이 통째로 사라지면(삭제) 후보의 모라 수가 원본과 크게 어긋난다. 대립 읽기끼리는
    # 길어야 2모라 차이인데(예: 刃 は/やいば), 삭제라면 그 낱말 전체 모라가 빠져 훨씬 크게
    # 벌어진다 — 넉넉한 상한(4)으로 "차이가 작다"만 못박는다.
    for word, text in _TABLE_EXAMPLES.items():
        default = wiki_pronunciation(text)
        for cand in pronunciation_candidates(text):
            if cand == default:
                continue
            delta = abs(len(cand.replace(" ", "")) - len(default.replace(" ", "")))
            assert delta <= 4, f"{word}: {text} 기본값 {default!r} vs 후보 {cand!r} (Δ{delta})"


def test_candidate_differs_from_default_only_at_the_matched_word():
    # 후보와 기본값의 유일한 차이가 그 낱말의 독음이어야 심판이 "독음 차이"만 재게 된다.
    # 공통 접두사/접미사를 떼어내면 남는 가운데 구간이 그 낱말 하나의 대립 읽기여야 한다.
    for word, text in _TABLE_EXAMPLES.items():
        default = wiki_pronunciation(text)
        alts = [c for c in pronunciation_candidates(text) if c != default]
        assert alts, f"{word}: {text}에서 대안이 하나도 안 나왔다"
        for cand in alts:
            matcher = SequenceMatcher(None, default, cand)
            blocks = [b for b in matcher.get_matching_blocks() if b.size]
            # 첫/마지막 매칭 블록 밖(=바뀐 구간)을 빼면 앞뒤가 원본과 글자 단위로 같아야 한다
            prefix_len = blocks[0].size if blocks and blocks[0].a == 0 and blocks[0].b == 0 else 0
            assert default[:prefix_len] == cand[:prefix_len]
            suffix_len = 0
            if blocks:
                last = blocks[-1]
                if last.a + last.size == len(default) and last.b + last.size == len(cand):
                    suffix_len = last.size
            assert (not suffix_len) or (default[-suffix_len:] == cand[-suffix_len:])


def test_this_period_reading_fuses_without_a_stray_space():
    # この期に及んで는 기본값에서 この/期가 서로 다른 문절(공백으로 분리)이지만,
    # このご는 관용구 하나로 통짜 읽기다 — 대안 후보에서 다시 갈라지면(코노 고니) 문절
    # 경계가 기본값과 달라져 "표기 차이"가 섞인다.
    text = "この期に及んで"
    default = wiki_pronunciation(text)
    assert default == "코노 키니 오욘데"
    cands = pronunciation_candidates(text)
    assert "코노고니 오욘데" in cands
    assert "코노 고니 오욘데" not in cands


# ---------------------------------------------------------------------------
# 제한 규칙 3: 한 줄에서 나오는 후보 수 상한
# ---------------------------------------------------------------------------


def test_multiple_ambiguous_words_in_one_line_each_get_their_own_candidate():
    # 弾く와 刃가 한 줄에 같이 있으면 각 낱말의 대립 읽기가 독립적으로 후보가 된다 —
    # 두 낱말을 동시에 바꾼 후보는 만들지 않는다(그러면 "어느 낱말 때문에 이겼는지" 해석이
    # 안 된다).
    text = "刃を弾く"
    default = wiki_pronunciation(text)
    cands = pronunciation_candidates(text)
    assert cands[0] == default
    assert "야이바오 히쿠" in cands  # 刃만 바뀜
    assert "하오 하지쿠" in cands  # 弾く만 바뀜
    assert "야이바오 하지쿠" not in cands  # 둘 다 바뀐 조합은 만들지 않는다
    assert len(cands) == 3


# ---------------------------------------------------------------------------
# 라틴 음차 공유 (표기 규칙이 후보와 기본값에서 갈라지면 안 된다)
# ---------------------------------------------------------------------------


def test_candidate_shares_the_wiki_convention_with_the_default():
    text = "何も言えない"
    for cand in pronunciation_candidates(text):
        # 둘 다 _render_pronunciation을 지나므로 문장부호 정규화·라틴 음차 규칙이 같다
        assert "이에나이" in cand
        assert cand.endswith("이에나이")


# ---------------------------------------------------------------------------
# 워커 배선 — 기본 후보와 라인별 마진
# ---------------------------------------------------------------------------


def _settings(**kw):
    # 심판은 실오디오 실측에서 해로웠으므로 기본값이 False다(근거는 AlignmentSettings 참조).
    # 이 파일은 기전 자체를 검증하므로 명시적으로 켠다 — 기본값에 기대면 안 된다.
    kw.setdefault("pron_referee", True)
    return AlignmentSettings(**kw)


def test_worker_puts_the_line_meta_pronunciation_first():
    lines = [_Line("何も言えない")]
    default = wiki_pronunciation("何も言えない")
    cands, margins = _referee_candidates(lines, [default], _settings())
    assert cands[0][0] == default
    assert len(cands[0]) > 1
    # 결정론 기본값 → 작은 마진
    assert margins[0] == _settings().pron_referee_margin


def test_worker_requires_a_larger_margin_for_a_human_written_pronunciation():
    # 사람이 쓴 발음은 결정론 출력과 다르다는 사실만으로 식별된다 (별도 플래그 없음).
    # 심판 대상에서 빼지 않고 마진만 크게 요구한다 — 사람도 후리가나를 놓치는 줄이 있다.
    lines = [_Line("私は歩く")]
    human = "와타시와 아루쿠"
    assert human != wiki_pronunciation("私は歩く")
    cands, margins = _referee_candidates(lines, [human], _settings())
    assert cands[0][0] == human, "사람 발음이 기본값이어야 한다"
    assert wiki_pronunciation("私は歩く") in cands[0], "결정론 독음도 후보로 남아야 한다"
    s = _settings()
    assert margins[0] == s.pron_referee_human_margin > s.pron_referee_margin


def test_worker_skips_lines_without_alternatives():
    # 발음이 없는 라인, 그리고 대안이 없는 라인은 심판 대상이 아니다 → 비용 0.
    # 私は歩く는 표 밖 낱말이라 대안이 없다 — 何も言えない만 대안이 있다.
    lines = [_Line("何も言えない"), _Line("私は歩く"), _Line("hello"), _Line("오늘")]
    cands, margins = _referee_candidates(
        lines,
        [wiki_pronunciation("何も言えない"), wiki_pronunciation("私は歩く"), "", ""],
        _settings(),
    )
    assert set(cands) == {0}
    assert set(margins) == {0}


def test_worker_respects_the_candidate_cap():
    lines = [_Line("刃を弾く 刃を弾く")]
    cands, _ = _referee_candidates(
        lines,
        [wiki_pronunciation("刃を弾く 刃を弾く")],
        _settings(pron_referee_max_candidates=3),
    )
    assert len(cands[0]) == 3


# ---------------------------------------------------------------------------
# 워커 배선 — 심판이 이긴 후보가 역매핑·표시·디버그에 실제로 반영되는가
# ---------------------------------------------------------------------------

_JA = "何も言えない"
_DEFAULT = wiki_pronunciation(_JA)
_WINNER = "나니모 이에나이"


class _FakeEngine:
    """align 호출 인자를 기록하고, 심판이 이긴 후보를 text로 돌려주는 엔진 대역.

    실제 채점은 tests/test_pron_referee.py가 합성 emission으로 검증한다 — 여기서는
    "엔진이 고른 독음이 워커의 역매핑·표시·디버그까지 흘러가는가"만 본다.
    """

    def __init__(self, winner: str | None):
        self.winner = winner
        self.kwargs = None
        self._last_referee = []
        self._last_heard = {}

    def align(self, audio, lyrics, language=None, **kwargs):
        from everyric2.inference.prompt import SyncResult, WordSegment

        self.kwargs = kwargs
        self.aligned_texts = [ln.text for ln in lyrics]  # 정렬에 실제로 들어간 텍스트
        results = []
        for i, ln in enumerate(lyrics):
            text = self.winner if (i == 0 and self.winner) else ln.text
            syllables = [ch for ch in text if ch != " "]
            step = 0.2
            results.append(
                SyncResult(
                    line_number=ln.line_number,
                    text=text,
                    start_time=0.0,
                    end_time=step * len(syllables),
                    word_segments=[
                        WordSegment(word=ch, start=step * k, end=step * (k + 1), confidence=0.5)
                        for k, ch in enumerate(syllables)
                    ],
                )
            )
        if self.winner:
            self._last_referee = [
                {
                    "line": 0,
                    "default": _DEFAULT,
                    "chosen": self.winner,
                    "margin": 0.15,
                    "gain": 0.42,
                    "frames": 60,
                    "scores": [[_DEFAULT, -3.1], [self.winner, -2.68]],
                }
            ]
        self._last_heard = {0: "나니모 이에나이"}
        return results


def _pron_align(winner):
    from everyric2.inference.prompt import LyricLine
    from everyric2.server.worker import _align_with_pronunciation, _pron_by_text

    engine = _FakeEngine(winner)
    lines = [LyricLine(text=_JA, line_number=1)]
    by_text = _pron_by_text([{"text": _JA, "pronunciation": _DEFAULT, "translation": "아무 말도 못 한다"}])
    results, pron_data = _align_with_pronunciation(
        engine, object(), lines, by_text, _settings()
    )
    return engine, results, pron_data


def test_worker_passes_candidates_to_the_engine():
    engine, _, _ = _pron_align(None)
    assert engine.kwargs["line_candidates"][0][0] == _DEFAULT
    assert _WINNER in engine.kwargs["line_candidates"][0]
    assert engine.kwargs["referee_margins"][0] == _settings().pron_referee_margin


def test_worker_adopts_the_winning_reading_for_display_and_backmapping():
    _, results, pron_data = _pron_align(_WINNER)
    # 표시 발음이 이긴 후보로 바뀐다 (何も → 나니모)
    assert pron_data[0]["pronunciation"] == _WINNER
    # 원문 라인은 그대로고, 음절 스팬은 이긴 후보의 음절 수만큼 잡힌다
    assert results[0].text == _JA
    assert pron_data[0]["pron_segments"]
    assert len(pron_data[0]["pron_segments"]) == len(_WINNER.replace(" ", ""))


def test_worker_carries_heard_text_and_referee_reasoning_into_debug_meta():
    # 실오디오 검증에서 후보별 점수를 못 보면 판정을 되짚을 수 없다 — 반드시 실려야 한다
    _, _, pron_data = _pron_align(_WINNER)
    assert pron_data[0]["heard"] == "나니모 이에나이"
    ref = pron_data[0]["referee"]
    assert ref["default"] == _DEFAULT and ref["chosen"] == _WINNER
    assert ref["scores"] == [[_DEFAULT, -3.1], [_WINNER, -2.68]]
    assert "line" not in ref  # 라인 번호는 세그먼트 자체가 들고 있다


def _mixed_align():
    """일어 줄 + **독음이 없는 한글 가창 줄**을 함께 정렬한다.

    실측(ba7YbGO2aq4): 한때 독음이 없는 줄은 빈 텍스트로 정렬 엔진에 들어가 그 줄이 정렬에서
    통째로 빠졌다. 타이밍은 앞뒤 사이로 보간돼 줄 시작은 그럭저럭 맞았지만 word_segments가
    없어 그 줄에서 가라오케 채움이 죽었다(align_coverage 0.9359, 빠진 5줄이 전부 한글 줄).
    """
    from everyric2.inference.prompt import LyricLine
    from everyric2.server.worker import _align_with_pronunciation, _pron_by_text

    engine = _FakeEngine(None)
    lines = [LyricLine(text=_JA, line_number=1), LyricLine(text="희미한", line_number=2)]
    # 한글 줄에는 독음이 없다 — wiki_pronunciation이 빈 문자열을 내는 것이 정상이다
    by_text = _pron_by_text([{"text": _JA, "pronunciation": _DEFAULT}])
    results, pron_data = _align_with_pronunciation(engine, object(), lines, by_text, _settings())
    return engine, results, pron_data


def test_a_line_without_a_reading_is_aligned_by_its_own_text():
    engine, _, _ = _mixed_align()
    # 일어 줄은 독음으로, 한글 줄은 **원문 그대로** 정렬에 들어간다 (빈 문자열이 아니다)
    assert engine.aligned_texts == [_DEFAULT, "희미한"]
    assert "" not in engine.aligned_texts


def test_a_line_without_a_reading_still_gets_syllable_spans():
    _, results, pron_data = _mixed_align()
    # 이것이 이 수정의 목적이다 — 그 줄에서도 글자별 스팬이 나와야 채움이 동작한다
    assert results[1].word_segments
    assert [w.word for w in results[1].word_segments] == list("희미한")
    assert all(w.confidence is not None for w in results[1].word_segments)
    # 스팬이 단조 증가하고 서로 겹치지 않는다
    for a, b in zip(results[1].word_segments, results[1].word_segments[1:]):
        assert a.end <= b.start
    # pron_segments는 **표시 독음의** 음절 스팬이다 — 표시할 독음이 없으니 없는 것이 맞다
    assert pron_data[1]["pron_segments"] is None
    assert pron_data[0]["pron_segments"]  # 일어 줄은 그대로 있다


def test_a_line_without_a_reading_shows_no_pronunciation():
    _, _, pron_data = _mixed_align()
    # 정렬에만 원문을 썼을 뿐이다 — 표시 독음에 원문을 넣으면 한글 아래 같은 한글이 또 찍힌다
    assert pron_data[1]["pronunciation"] is None
    assert pron_data[0]["pronunciation"] == _DEFAULT  # 일어 줄은 그대로


def test_line_meta_remerge_does_not_revert_a_referee_decision():
    # 캐시 재사용·늦은 메타 병합 경로가 심판 판정을 조용히 되돌리면, 표시 발음과
    # pron_segments(이긴 후보의 음절 스팬)의 음절 수가 어긋난다.
    from everyric2.server.worker import merge_line_meta

    switched = {
        "text": _JA,
        "pronunciation": _WINNER,
        "pron_segments": [{"text": "나", "start": 0.0, "end": 0.2}],
        "debug": {"referee": {"default": _DEFAULT, "chosen": _WINNER}},
    }
    kept = {
        "text": _JA,
        "pronunciation": _DEFAULT,
        "debug": {"referee": {"default": _DEFAULT, "chosen": _DEFAULT}},
    }
    plain = {"text": _JA}
    meta = [{"text": _JA, "pronunciation": _DEFAULT, "translation": "아무 말도 못 한다"}]

    merge_line_meta([switched, kept, plain], meta)
    assert switched["pronunciation"] == _WINNER, "심판 판정이 재병합으로 되돌아갔다"
    assert switched["translation"] == "아무 말도 못 한다", "번역 병합은 그대로여야 한다"
    # 심판이 기본값을 유지한 라인과 심판이 안 돈 라인은 예전대로 병합된다
    assert kept["pronunciation"] == _DEFAULT
    assert plain["pronunciation"] == _DEFAULT


def test_worker_omits_candidates_when_the_referee_is_off():
    from everyric2.inference.prompt import LyricLine
    from everyric2.server.worker import _align_with_pronunciation, _pron_by_text

    engine = _FakeEngine(None)
    lines = [LyricLine(text=_JA, line_number=1)]
    by_text = _pron_by_text([{"text": _JA, "pronunciation": _DEFAULT}])
    _align_with_pronunciation(engine, object(), lines, by_text, _settings(pron_referee=False))
    assert engine.kwargs["line_candidates"] is None
    assert engine.kwargs["referee_margins"] is None

    # align_settings를 아예 주지 않는 기존 호출부도 그대로 동작해야 한다
    engine2 = _FakeEngine(None)
    _align_with_pronunciation(engine2, object(), lines, by_text)
    assert engine2.kwargs["line_candidates"] is None
