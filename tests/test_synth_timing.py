"""붕괴 곡/누출 라인 균등 비례 타이밍 재합성 회귀 테스트.

posterior가 바닥인 곡(熱異常: corr(라인 conf,|잔차|)=-0.19)은 라인 경계를 스냅/가드로
잡아도 word/pron_segments 내부 분포가 CTC 잔해라 무의미하다. 보정 마지막 단계에서
(a)곡 저신뢰 예비 스위치(기본 0=비활성), (b)leak 라벨 라인, (c)물리적으로 불가능한
내부 분포 라인의 라인 내부 타이밍을 글자/음절 수 균등 비례로 재합성한다
(confidence=None). CTC가 제대로 잡은 라인은 보존. 라인 경계와 멜로디 노트는 불변.
"""
from everyric2.inference.prompt import SyncResult, WordSegment
from everyric2.server.worker import (
    _full_coverage_words,
    _impossible_word_distribution,
    _resynth_pron_segments,
    _resynth_word_segments,
    _synthesize_collapsed_timing,
)


def _mkline(text, start, end, conf=0.001):
    # 초기 word 타이밍은 왜곡값(전부 라인 시작에 뭉침) — 재합성이 이를 대체하는지 확인용
    ws = [WordSegment(word=c, start=start, end=start, confidence=conf) for c in text]
    return SyncResult(text=text, start_time=start, end_time=end, word_segments=ws)


def _starts(ws):
    return [w.start for w in ws]


# ---- _resynth_word_segments: 글자 수 균등 비례, 경계 정확, 단조 ----------------


def test_resynth_word_uniform_exact_boundaries_and_monotonic():
    r = _mkline("あいうえお", 10.0, 20.0)
    _resynth_word_segments(r.word_segments, r.start_time, r.end_time)
    ws = r.word_segments
    assert ws[0].start == 10.0 and ws[-1].end == 20.0  # 경계 정확
    assert _starts(ws) == sorted(_starts(ws))            # 단조
    # 5글자 균등: 각 2.0s, 이어붙음
    assert all(abs((w.end - w.start) - 2.0) < 1e-9 for w in ws)
    assert all(abs(ws[i].end - ws[i + 1].start) < 1e-9 for i in range(len(ws) - 1))
    assert all(w.confidence is None for w in ws)          # conf 폐기


def test_resynth_word_char_proportional_multichar_tokens():
    r = SyncResult(
        text="繰り返しあ", start_time=0.0, end_time=10.0,
        word_segments=[WordSegment("繰り返し", 0, 0, 0.5), WordSegment("あ", 0, 0, 0.5)],
    )
    _resynth_word_segments(r.word_segments, 0.0, 10.0)
    # 4글자 : 1글자 = 8s : 2s
    assert abs(r.word_segments[0].end - 8.0) < 1e-9
    assert abs(r.word_segments[1].start - 8.0) < 1e-9 and r.word_segments[1].end == 10.0


def test_resynth_word_noop_on_degenerate_span():
    r = _mkline("あい", 5.0, 5.0)  # end<=start
    before = [(w.start, w.end) for w in r.word_segments]
    _resynth_word_segments(r.word_segments, 5.0, 5.0)
    assert [(w.start, w.end) for w in r.word_segments] == before


# ---- _resynth_pron_segments: 음절 수 균등 비례 --------------------------------


def test_resynth_pron_uniform():
    segs = [{"text": "모", "start": 3.0, "end": 3.1}, {"text": "오", "start": 3.1, "end": 3.2},
            {"text": ".", "start": 3.2, "end": 3.3}, {"text": "케", "start": 3.3, "end": 3.4}]
    _resynth_pron_segments(segs, 100.0, 108.0)  # 스냅으로 라인이 100~108s로 이동한 상황
    assert segs[0]["start"] == 100.0 and segs[-1]["end"] == 108.0
    assert [s["start"] for s in segs] == [100.0, 102.0, 104.0, 106.0]
    assert all(segs[i]["end"] <= segs[i + 1]["start"] + 1e-9 for i in range(len(segs) - 1))


# ---- _synthesize_collapsed_timing: 대상 선정(a)∪(b) ---------------------------


def _fixture():
    results = [_mkline("あいう", 0.0, 3.0), _mkline("かきく", 3.0, 6.0), _mkline("さしす", 6.0, 9.0)]
    pron_data = {
        0: {"pron_segments": [{"text": "a", "start": 0, "end": 0}, {"text": "b", "start": 0, "end": 0}]},
        1: {"pron_segments": None},  # 이미 무효화 — 그대로 둬야
    }
    return results, pron_data


def test_low_conf_song_resynths_all_lines():
    results, pron_data = _fixture()
    synth = _synthesize_collapsed_timing(results, pron_data, {}, song_conf=0.0005, threshold=0.002)
    assert synth == {0, 1, 2}
    for r in results:
        assert r.word_segments[0].start == r.start_time and r.word_segments[-1].end == r.end_time
        assert all(w.confidence is None for w in r.word_segments)
    # 무효화된 pron_segments(라인1)는 그대로 None
    assert pron_data[1]["pron_segments"] is None
    # 라인0 pron_segments는 라인 [0,3]에 균등 재합성
    assert pron_data[0]["pron_segments"][0]["start"] == 0.0
    assert abs(pron_data[0]["pron_segments"][1]["start"] - 1.5) < 1e-9


def test_normal_song_unchanged():
    results, pron_data = _fixture()
    before = [[(w.start, w.end, w.confidence) for w in r.word_segments] for r in results]
    synth = _synthesize_collapsed_timing(results, pron_data, {}, song_conf=0.01, threshold=0.002)
    assert synth == set()
    assert [[(w.start, w.end, w.confidence) for w in r.word_segments] for r in results] == before


def test_only_leak_lines_fire_when_song_conf_ok():
    results, pron_data = _fixture()
    synth = _synthesize_collapsed_timing(
        results, pron_data, {1: ["leak"]}, song_conf=0.01, threshold=0.002
    )
    assert synth == {1}
    # 라인0/2는 손대지 않음 (초기 왜곡값 유지: 전부 start에 뭉침)
    assert all(w.start == 0.0 for w in results[0].word_segments)
    assert results[1].word_segments[-1].end == 6.0  # 재합성됨


def test_threshold_zero_disables_low_conf_but_keeps_leak():
    results, pron_data = _fixture()
    synth = _synthesize_collapsed_timing(
        results, pron_data, {2: ["leak"]}, song_conf=0.0001, threshold=0.0
    )
    assert synth == {2}  # 저신뢰 전 라인 경로는 꺼지고 leak 라인만


def test_line_confidence_preserved_from_word_geomean():
    # r.confidence가 None이면 재합성(word conf None) 전에 word 기하평균으로 백필 → quality 보존
    r = _mkline("あい", 0.0, 2.0, conf=0.004)  # r.confidence=None, word conf=0.004
    assert r.confidence is None
    _synthesize_collapsed_timing([r], None, {}, song_conf=0.0005, threshold=0.002)
    assert abs(r.confidence - 0.004) < 1e-9  # 백필됨
    assert all(w.confidence is None for w in r.word_segments)


# ---- 직렬화 _full_coverage_words와 조합 ---------------------------------------


# ---- (c) 물리적으로 불가능한 내부 분포 — 구조 신호 선별 -------------------------


def test_crammed_line_resynthed_without_song_floor():
    # 라인 span 10s인데 8글자가 0.2s에 뭉침(40자/s) → 잔해 판정, 곡 임계 없이도 재합성
    ws = [WordSegment(word=c, start=1.0 + i * 0.025, end=1.0 + (i + 1) * 0.025, confidence=0.5)
          for i, c in enumerate("あいうえおかきく")]
    r = SyncResult(text="あいうえおかきく", start_time=0.0, end_time=10.0, word_segments=ws)
    synth = _synthesize_collapsed_timing(
        [r], None, {}, song_conf=0.01, threshold=0.0, max_char_rate=11.0)
    assert synth == {0}
    assert r.word_segments[0].start == 0.0 and r.word_segments[-1].end == 10.0


def test_fast_rap_line_kept():
    # 초고속 랩: 라인 자체가 짧아(1s) 글자가 라인 폭을 다 덮음 — 뭉침 아님, CTC 보존
    ws = [WordSegment(word=c, start=i * 0.125, end=(i + 1) * 0.125, confidence=0.5)
          for i, c in enumerate("あいうえおかきく")]
    r = SyncResult(text="あいうえおかきく", start_time=0.0, end_time=1.0, word_segments=ws)
    synth = _synthesize_collapsed_timing(
        [r], None, {}, song_conf=0.01, threshold=0.0, max_char_rate=11.0)
    assert synth == set()
    assert r.word_segments[0].confidence == 0.5


def test_out_of_bounds_words_resynthed():
    # 라인 [100,110]인데 word들이 80s대를 가리킴 — 경계 이탈 잔해
    ws = [WordSegment(word=c, start=80.0 + i, end=81.0 + i, confidence=0.5)
          for i, c in enumerate("あいうえ")]
    r = SyncResult(text="あいうえ", start_time=100.0, end_time=110.0, word_segments=ws)
    synth = _synthesize_collapsed_timing(
        [r], None, {}, song_conf=0.01, threshold=0.0, max_char_rate=11.0)
    assert synth == {0}


# ---- (c-③) 선두 글자 고립 — 라인 단위 신호가 곡 평균 임계에 묻히던 구멍 ----------


def _chant_line(start, end, lead_start, body_start, body_step, n_body):
    """선두 1글자만 앞에 고립되고 나머지가 뒤에 몰린 챈트 라인."""
    ws = [WordSegment(word="어", start=lead_start, end=lead_start + 0.2, confidence=2.4e-05)]
    ws += [
        WordSegment(
            word="글", start=body_start + i * body_step,
            end=body_start + (i + 1) * body_step, confidence=2.4e-05,
        )
        for i in range(n_body)
    ]
    return SyncResult(text="어" + "글" * n_body, start_time=start, end_time=end, word_segments=ws)


def test_isolated_leading_char_is_flagged():
    # XKZIQlqVjjk 코러스 챈트 재현: 동일 음절 4연속(Approved×4 → 어프루브드×4)이라
    # posterior가 평평해져 선두 1글자만 일찍 걸리고 나머지 34자가 뒤에 몰린다.
    # 라인 conf 2.4e-05는 곡 평균(0.00439) 기준 임계 0.002에 안 걸려 어디서도 안 잡혔다.
    r = _chant_line(0.0, 10.0, lead_start=0.0, body_start=5.0, body_step=0.14, n_body=34)
    ws = r.word_segments
    # ① 뭉침 검사는 통과해버린다 — 글자 폭(9.76)이 라인 폭의 절반을 넘기 때문
    assert (max(w.end for w in ws) - min(w.start for w in ws)) >= (10.0 - 0.0) * 0.5
    assert _impossible_word_distribution(ws, 0.0, 10.0, 11.0) is True


def test_isolated_leading_char_line_is_resynthed():
    r = _chant_line(0.0, 10.0, lead_start=0.0, body_start=5.0, body_step=0.14, n_body=34)
    synth = _synthesize_collapsed_timing(
        [r], None, {}, song_conf=0.00439, threshold=0.002, max_char_rate=11.0)
    assert synth == {0}
    assert r.word_segments[0].start == 0.0 and r.word_segments[-1].end == 10.0


def test_evenly_spread_lines_are_not_flagged_as_isolated_leader():
    # 정상 라인 오판 방지(초고속 곡 정상 밀집 라인 36줄을 크램으로 오판한 회귀 사고 재발 방지):
    # 글자가 고르게 퍼진 라인은 4글자 이상이면 선두 간격이 라인 폭의 25% 이하다.
    for n, span in ((4, 1.0), (8, 2.0), (20, 8.0), (40, 12.0)):
        step = span / n
        ws = [
            WordSegment(word="글", start=i * step, end=(i + 1) * step, confidence=0.01)
            for i in range(n)
        ]
        assert _impossible_word_distribution(ws, 0.0, span, 11.0) is False, (n, span)


def test_held_first_syllable_is_not_flagged():
    # 첫 음절을 2초 끄는 정상 라인(라인 8초)은 고립이 아니다 — 나머지 글자가 라인 폭을 덮는다
    ws = [WordSegment(word="あ", start=0.0, end=2.0, confidence=0.01)]
    ws += [
        WordSegment(word=c, start=2.0 + i * 0.5, end=2.0 + (i + 1) * 0.5, confidence=0.01)
        for i, c in enumerate("いうえお")
    ]
    assert _impossible_word_distribution(ws, 0.0, 8.0, 11.0) is False


def test_isolated_leader_check_respects_min_token_count_and_disable_switch():
    # 토큰 3개 이하는 판정하지 않고, max_char_rate=0이면 구조 검사 자체가 꺼진다
    short = [
        WordSegment(word="글", start=s, end=s + 0.1, confidence=0.01) for s in (0.0, 9.0, 9.2)
    ]
    assert _impossible_word_distribution(short, 0.0, 10.0, 11.0) is False
    r = _chant_line(0.0, 10.0, lead_start=0.0, body_start=5.0, body_step=0.14, n_body=34)
    assert _impossible_word_distribution(r.word_segments, 0.0, 10.0, 0.0) is False


def test_impossible_distribution_disabled_at_zero_rate():
    # max_char_rate=0(비활성)이면 뭉침 극단도 구조 검사에 안 걸린다
    ws = [WordSegment(word=c, start=1.0, end=1.0, confidence=0.5) for c in "あいうえ"]
    assert _impossible_word_distribution(ws, 0.0, 10.0, 0.0) is False
    assert _impossible_word_distribution(ws, 0.0, 10.0, 11.0) is True  # width 0 = 극단 뭉침


# ---- 직렬화 _full_coverage_words와 조합 ---------------------------------------


def test_synthesized_words_still_full_cover_body():
    r = _mkline("ボクは 生まれ", 5.0, 11.0)  # 공백 포함 본문
    # 공백은 word_segments에 없다고 가정(정렬 토큰은 글자만): 토큰에서 공백 제거
    r.word_segments = [w for w in r.word_segments if w.word != " "]
    _synthesize_collapsed_timing([r], None, {}, song_conf=0.0005, threshold=0.002)
    out = _full_coverage_words(r.text, r.word_segments)
    assert "".join(o["word"] for o in out) == r.text          # join==본문
    assert [o["start"] for o in out] == sorted(o["start"] for o in out)  # 단조
