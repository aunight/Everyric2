"""worker의 표기별 발음 부착(`attach_pron_variants`)과 생성 번역의 언어 분리 — 순수 함수 단위.

실오디오도 DB도 쓰지 않는다. 세그먼트는 정렬 파이프라인이 만드는 모양(text + words 글자
스팬 + pronunciation)을 손으로 합성하고, 시각은 글자당 0.5초로 깔아 단조성을 눈으로 검산할
수 있게 했다.
"""

from everyric2.server.worker import (
    attach_pron_variants,
    job_target_lang,
    translation_layer_lines,
)
from everyric2.text.pron_style import candidate_token_sets
from everyric2.text.reading import mora_segments_for_line

# 골든 스냅샷(tests/test_pron_golden.py)에 있는 줄 — 한글 독음과 romaji가 둘 다 실측값이다
NEKURA = "アルバイトはネクラモード"
NEKURA_HANGUL = "아루바이토와 네쿠라 모오도"
NEKURA_ROMAJI = "arubaito wa nekura moodo"

# 애매 낱말 刃(は/やいば) — 심판이 뒤집으면 모라 수가 4에서 6으로 늘어난다
YAIBA = "刃を研ぐ"
YAIBA_DEFAULT_HANGUL = "하오 토구"
YAIBA_CHOSEN_HANGUL = "야이바오 토구"


def _words(text: str, step: float = 0.5) -> list[dict]:
    """비공백 글자별 (start, end) 스팬 — 정렬 word_segments(공백을 만들지 않는 CTC 토큰)와
    같은 모양이다. ``_full_coverage_words``가 직렬화에서 실제로 만드는 ``seg["words"]``는
    이것과 **다르다**(라인 전체 글자를 공백까지 포함해 1:1로 덮는다) — 그 모양은
    ``_full_words``가 낸다."""
    return [
        {"word": ch, "start": i * step, "end": (i + 1) * step}
        for i, ch in enumerate(text)
        if not ch.isspace()
    ]


def _full_words(text: str, step: float = 0.5) -> list[dict]:
    """글자별(공백 포함) (start, end) 스팬 — ``_full_coverage_words``가 직렬화에서 실제로
    만드는 ``seg["words"]``와 같은 모양(실측: "옛날 머나먼 그 어느 마을엔" → words 15개,
    그중 공백 4개). ko 분기의 ``_ko_char_time``이 공백 항목을 걸러내지 않으면 원문의
    비공백 글자와 개수가 어긋나 kana/romaji 시각이 전멸했던 실사용 버그(N_vYUNEktsA)의
    재현 픽스처다."""
    return [{"word": ch, "start": i * step, "end": (i + 1) * step} for i, ch in enumerate(text)]


def _seg(text: str, pronunciation: str, *, words: bool = True) -> dict:
    seg: dict = {"text": text, "start": 0.0, "end": len(text) * 0.5, "pronunciation": pronunciation}
    if words:
        seg["words"] = _words(text)
    return seg


def _rebuild(segments: list[dict]) -> str:
    """모라 스팬을 표시 문자열로 되돌린다 — «표시=세그» 단일 소스 불변식 검산용."""
    return "".join(s["text"] + (" " if s.get("space") else "") for s in segments).strip()


def _chosen_tokens(text: str, chosen: str) -> list:
    rendered, token_sets = candidate_token_sets(text)
    return token_sets[rendered.index(chosen)]


def test_attaches_hangul_and_romaji():
    seg = _seg(NEKURA, NEKURA_HANGUL)
    attach_pron_variants(seg)

    assert seg["pron"]["hangul"] == seg["pronunciation"] == NEKURA_HANGUL
    assert seg["pron"]["romaji"] == NEKURA_ROMAJI


def test_romaji_segments_are_monotonic_and_rebuild_the_display():
    seg = _seg(NEKURA, NEKURA_HANGUL)
    attach_pron_variants(seg)

    segments = seg["pron_segs"]["romaji"]
    assert len(segments) == 12  # 모라 수
    assert _rebuild(segments) == seg["pron"]["romaji"]
    for prev, cur in zip(segments, segments[1:]):
        assert cur["start"] >= prev["end"]
        assert cur["end"] >= cur["start"]
    # 라인 구간을 벗어나지 않는다 (글자 스팬에서 파생됐으므로)
    assert segments[0]["start"] >= 0.0
    assert segments[-1]["end"] <= seg["end"]


def test_legacy_hangul_fields_are_untouched():
    seg = _seg(NEKURA, NEKURA_HANGUL)
    seg["pron_segments"] = [{"text": "아", "start": 0.0, "end": 0.5, "resolved": True}]
    attach_pron_variants(seg)

    assert seg["pronunciation"] == NEKURA_HANGUL
    assert seg["pron_segments"] == [{"text": "아", "start": 0.0, "end": 0.5, "resolved": True}]


def test_referee_tokens_switch_the_reading():
    default_seg = _seg(YAIBA, YAIBA_DEFAULT_HANGUL)
    attach_pron_variants(default_seg)
    assert default_seg["pron"]["romaji"] == "ha o togu"
    assert len(default_seg["pron_segs"]["romaji"]) == 4

    # 심판이 やいば를 골랐다 — romaji도 그 읽기를 따라야 한다(모라 4 → 6)
    chosen_seg = _seg(YAIBA, YAIBA_CHOSEN_HANGUL)
    attach_pron_variants(chosen_seg, referee_tokens=_chosen_tokens(YAIBA, YAIBA_CHOSEN_HANGUL))

    assert chosen_seg["pron"]["hangul"] == YAIBA_CHOSEN_HANGUL
    assert chosen_seg["pron"]["romaji"] == "yaiba o togu"
    segments = chosen_seg["pron_segs"]["romaji"]
    assert len(segments) == 6
    assert _rebuild(segments) == "yaiba o togu"


def test_referee_switched_segment_gets_no_romaji_without_tokens():
    # 심판이 바꾼 줄인데 이긴 읽기의 토큰 열이 없다(캐시 재사용·늦은 병합 경로).
    # 기본 읽기로 렌더하면 화면의 한글 독음과 다른 낱말이 찍히므로 표기를 붙이지 않는다.
    seg = _seg(YAIBA, YAIBA_CHOSEN_HANGUL)
    seg["debug"] = {"referee": {"default": YAIBA_DEFAULT_HANGUL, "chosen": YAIBA_CHOSEN_HANGUL}}
    attach_pron_variants(seg)

    assert seg["pron"] == {"hangul": YAIBA_CHOSEN_HANGUL}
    assert "pron_segs" not in seg


def test_referee_untouched_segment_still_gets_romaji():
    # 심판이 돌긴 했지만 기본값을 그대로 유지한 줄은 기본 읽기가 곧 정답이다
    seg = _seg(YAIBA, YAIBA_DEFAULT_HANGUL)
    seg["debug"] = {"referee": {"default": YAIBA_DEFAULT_HANGUL, "chosen": YAIBA_DEFAULT_HANGUL}}
    attach_pron_variants(seg)

    assert seg["pron"]["romaji"] == "ha o togu"


def test_is_idempotent():
    seg = _seg(YAIBA, YAIBA_CHOSEN_HANGUL)
    attach_pron_variants(seg, referee_tokens=_chosen_tokens(YAIBA, YAIBA_CHOSEN_HANGUL))
    before = {"pron": dict(seg["pron"]), "pron_segs": {k: list(v) for k, v in seg["pron_segs"].items()}}

    # 두 번째 호출은 다른 읽기를 들고 와도 이미 붙은 값을 덮지 않는다
    attach_pron_variants(seg)

    assert seg["pron"] == before["pron"]
    assert seg["pron_segs"] == before["pron_segs"]


def test_display_survives_when_timing_is_unavailable():
    # 글자 스팬이 없으면(라인 타이밍만 있는 줄) 표기 문자열만 남고 확장이 그라데이션으로 폴백한다
    seg = _seg(NEKURA, NEKURA_HANGUL, words=False)
    attach_pron_variants(seg)

    assert seg["pron"]["romaji"] == NEKURA_ROMAJI
    assert "pron_segs" not in seg


def test_display_survives_when_char_spans_do_not_match_the_text():
    # words가 이 줄의 글자가 아니면 시각 환산이 성립하지 않는다 — 표시만 남긴다
    seg = _seg(NEKURA, NEKURA_HANGUL)
    seg["words"] = _words("전혀 다른 줄")
    attach_pron_variants(seg)

    assert seg["pron"]["romaji"] == NEKURA_ROMAJI
    assert "pron_segs" not in seg


def test_skips_ja_segment_without_pronunciation():
    # ja 곡 분기는 ``pronunciation``(독음) 필드가 필수다 — 없으면 hangul/romaji 둘 다 생략.
    no_pron = _seg(NEKURA, "")
    attach_pron_variants(no_pron)
    assert "pron" not in no_pron


def test_skips_segment_without_ja_ko_or_latin_text():
    # 숫자·기호뿐인 줄은 세 분기(ja/ko/라틴) 어디에도 안 걸린다.
    symbols_only = {"text": "…！", "start": 0.0, "end": 0.5}
    attach_pron_variants(symbols_only)
    assert "pron" not in symbols_only


def test_ko_segment_gets_kana_and_romaja():
    # ko 곡 세그는 ``pronunciation`` 필드가 없어도(원문 한글 자체가 독음) kana/romaji가 붙는다.
    seg = _seg("사랑해", "", words=True)
    attach_pron_variants(seg)

    assert seg["pron"] == {"kana": "サランヘ", "romaji": "saranghae"}
    assert "hangul" not in seg["pron"]  # 원문이 이미 표시라 hangul 키는 만들지 않는다


def test_ko_segment_kana_segs_are_monotonic_and_bisect_the_coda():
    seg = _seg("사랑해", "", words=True)
    attach_pron_variants(seg)

    segments = seg["pron_segs"]["kana"]
    # 사(1모라) + 랑(받침 ㅇ→independent ン, 2모라) + 해(1모라) = 4모라
    assert [s["text"] for s in segments] == ["サ", "ラ", "ン", "ヘ"]
    assert "".join(s["text"] for s in segments) == seg["pron"]["kana"]
    for prev, cur in zip(segments, segments[1:]):
        assert cur["start"] >= prev["end"]
        assert cur["end"] >= cur["start"]
    # 받침 이등분: 랑(글자 스팬 0.5~1.0)의 두 모라(ラ/ン)가 그 구간을 균등 분할한다
    lang_span = _words("사랑해")[1]
    ra, n = segments[1], segments[2]
    assert ra["start"] == lang_span["start"]
    assert ra["end"] == n["start"] == (lang_span["start"] + lang_span["end"]) / 2
    assert n["end"] == lang_span["end"]


def test_ko_segment_romaja_segs_are_monotonic_and_rebuild_the_display():
    seg = _seg("사랑해", "", words=True)
    attach_pron_variants(seg)

    segments = seg["pron_segs"]["romaji"]
    # 한 글자 = 로마자 한 덩이(받침이 갈라지지 않는다) — kana처럼 이등분이 없다
    assert [s["text"] for s in segments] == ["sa", "rang", "hae"]
    assert "".join(s["text"] for s in segments) == seg["pron"]["romaji"]
    for prev, cur in zip(segments, segments[1:]):
        assert cur["start"] >= prev["end"]
        assert cur["end"] >= cur["start"]
    # 글자 스팬을 그대로 옮겨 붙인다 — 랑의 스팬과 정확히 같아야 한다(균등분할 없음)
    lang_span = _words("사랑해")[1]
    assert segments[1]["start"] == lang_span["start"]
    assert segments[1]["end"] == lang_span["end"]


def test_ko_segment_kana_and_romaja_segs_survive_words_with_blank_entries():
    # 실사용 버그 재현: _full_coverage_words가 만드는 words는 공백도 항목으로 포함한다.
    # 필터링 없이 원문 비공백 글자와 zip하면 전 줄에서 개수 불일치 → kana/romaji segs 전멸.
    from everyric2.text.ko_reading import hangul_line_moras, hangul_line_romaja_syllables

    text = "사랑해 진짜"
    seg = _seg(text, "", words=False)
    seg["words"] = _full_words(text)  # 공백 포함 — 실제 _full_coverage_words 모양
    attach_pron_variants(seg)

    kana_segments = seg["pron_segs"]["kana"]
    romaja_segments = seg["pron_segs"]["romaji"]

    assert len(kana_segments) == len(hangul_line_moras(text)) > 0
    assert len(romaja_segments) == len(hangul_line_romaja_syllables(text)) > 0

    for segments in (kana_segments, romaja_segments):
        for prev, cur in zip(segments, segments[1:]):
            assert cur["start"] >= prev["end"]
            assert cur["end"] >= cur["start"]


def test_ko_segment_display_survives_when_timing_is_unavailable():
    seg = _seg("좋아해 그대를", "", words=False)
    attach_pron_variants(seg)

    assert seg["pron"]["kana"] and seg["pron"]["romaji"]
    assert "pron_segs" not in seg


def test_latin_segment_gets_kana_display_only():
    # 라틴 곡은 일본어권용 가나 근사만 표시로 붙는다 — CTC 정렬이 라틴 위에서 약해서
    # (latin_hangul 모듈 실측) pron_segs는 만들지 않는다.
    from everyric2.text.ko_reading import latin_to_kana

    seg = _seg("Take it easy", "", words=True)
    attach_pron_variants(seg)

    assert seg["pron"] == {"kana": latin_to_kana("Take it easy")}
    assert "pron_segs" not in seg
    assert "romaji" not in seg["pron"]  # 라틴 곡 세그는 romaji 표기를 만들지 않는다(원문이 이미 로마자)


def test_mora_segments_follow_the_given_tokens():
    # attach가 기대는 계약: 같은 글자 스팬이라도 토큰 열을 주면 모라 수가 그 읽기를 따른다
    char_spans = [(w["word"], w["start"], w["end"]) for w in _words(YAIBA)]

    assert len(mora_segments_for_line(char_spans, YAIBA)) == 4
    chosen = _chosen_tokens(YAIBA, YAIBA_CHOSEN_HANGUL)
    assert len(mora_segments_for_line(char_spans, YAIBA, tokens=chosen)) == 6


def test_mora_segments_return_none_without_spans():
    assert mora_segments_for_line([], YAIBA) is None
    assert mora_segments_for_line([("刃", 0.0, 0.5)], "   ") is None


def test_referee_token_set_finds_the_winning_reading():
    from everyric2.server.worker import _referee_token_set

    tokens = _referee_token_set(YAIBA, YAIBA_CHOSEN_HANGUL)
    seg = _seg(YAIBA, YAIBA_CHOSEN_HANGUL)
    attach_pron_variants(seg, referee_tokens=tokens)
    assert seg["pron"]["romaji"] == "yaiba o togu"

    # 후보에 없는 문자열(사람이 손으로 쓴 발음 등)은 None — 기본 읽기로 조용히 떨어진다
    assert _referee_token_set(YAIBA, "엉뚱한 독음") is None


class _FakeEngine:
    """심판이 이긴 후보를 text로 돌려주는 엔진 대역 (tests/test_pron_candidates.py와 같은 모양).

    여기서 보는 것은 채점이 아니라 배선이다: 이긴 읽기의 토큰 열이 pron_data를 거쳐
    직렬화의 attach_pron_variants까지 흘러가는가.
    """

    def __init__(self, winner: str):
        self.winner = winner
        self._last_referee: list[dict] = []
        self._last_heard: dict = {}
        self._last_heard_spans: dict = {}

    def align(self, audio, lyrics, language=None, **kwargs):
        from everyric2.inference.prompt import SyncResult, WordSegment

        syllables = [ch for ch in self.winner if ch != " "]
        step = 0.2
        self._last_referee = [
            {
                "line": 0,
                "default": YAIBA_DEFAULT_HANGUL,
                "chosen": self.winner,
                "margin": 0.15,
                "gain": 0.42,
                "frames": 60,
                "scores": [[YAIBA_DEFAULT_HANGUL, -3.1], [self.winner, -2.68]],
            }
        ]
        return [
            SyncResult(
                line_number=lyrics[0].line_number,
                text=self.winner,
                start_time=0.0,
                end_time=step * len(syllables),
                word_segments=[
                    WordSegment(word=ch, start=step * k, end=step * (k + 1), confidence=0.5)
                    for k, ch in enumerate(syllables)
                ],
            )
        ]


def test_referee_reading_reaches_the_serialized_segment():
    from everyric2.config.settings import AlignmentSettings
    from everyric2.inference.prompt import LyricLine
    from everyric2.server.worker import _align_with_pronunciation, _pron_by_text

    engine = _FakeEngine(YAIBA_CHOSEN_HANGUL)
    lines = [LyricLine(text=YAIBA, line_number=1)]
    by_text = _pron_by_text([{"text": YAIBA, "pronunciation": YAIBA_DEFAULT_HANGUL}])
    results, pron_data = _align_with_pronunciation(
        engine, object(), lines, by_text, AlignmentSettings(pron_referee=True)
    )

    pd = pron_data[0]
    assert pd["pronunciation"] == YAIBA_CHOSEN_HANGUL
    assert pd["tokens"] is not None  # 이긴 읽기의 토큰 열이 실려 나왔다

    # 직렬화 루프가 하는 일 그대로 — 세그를 세우고 그 토큰 열로 표기를 얹는다
    seg = {
        "text": results[0].text,
        "start": results[0].start_time,
        "end": results[0].end_time,
        "pronunciation": pd["pronunciation"],
        "words": [
            {"word": w.word, "start": w.start, "end": w.end} for w in results[0].word_segments
        ],
        "debug": {"referee": pd["referee"]},
    }
    attach_pron_variants(seg, referee_tokens=pd["tokens"])

    assert seg["pron"]["hangul"] == YAIBA_CHOSEN_HANGUL
    assert seg["pron"]["romaji"] == "yaiba o togu"
    assert _rebuild(seg["pron_segs"]["romaji"]) == "yaiba o togu"


def test_translation_layer_lines_keeps_only_translated_pairs():
    items = [
        {"text": "アルバイトは", "translation": "아르바이트는"},
        {"text": "  ", "translation": "공백 줄"},
        {"text": "간주", "translation": "   "},
        {"text": "ネクラモード", "translation": "네쿠라 모드"},
    ]
    assert translation_layer_lines(items) == [
        {"text": "アルバイトは", "translation": "아르바이트는"},
        {"text": "ネクラモード", "translation": "네쿠라 모드"},
    ]
    assert translation_layer_lines(None) == []


def test_job_target_lang_defaults_to_ko():
    class _Job:
        def __init__(self, target_lang=None):
            if target_lang is not None:
                self.target_lang = target_lang

    assert job_target_lang(_Job()) == "ko"  # 컬럼이 없던 시절의 잡 행
    assert job_target_lang(_Job("")) == "ko"
    assert job_target_lang(_Job(" en ")) == "en"
