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


# ---------------------------------------------------------------------------
# ④ 낱말이 프레임에 눌린 라인 — XKZIQlqVjjk `Approved`×4 실측에서 나온 게이트
# ---------------------------------------------------------------------------


def _word_line(words, per_word_span):
    """낱말별 (글자열, [start, end])로 word_segments를 만든다. 공백도 토큰으로 넣는다."""
    ws = []
    for i, (w, (st, en)) in enumerate(zip(words, per_word_span)):
        if i:
            ws.append(WordSegment(word=" ", start=st, end=st, confidence=0.5))
        step = (en - st) / max(1, len(w))
        for k, ch in enumerate(w):
            ws.append(WordSegment(word=ch, start=st + k * step,
                                  end=st + (k + 1) * step, confidence=0.5))
    return ws


def test_repeats_pressed_into_single_frames_are_resynthesised():
    """실측 사례: `Approved`×4의 구간이 [3.3, 0.02, 0.02, 0.02]초 — 8글자 낱말이 프레임 1개.

    ①(라인 전체 폭)은 글자가 라인 폭을 다 덮어서 통과하고, ③(선두 고립)은 임계 미달로
    통과했다(실측 0.22s vs 임계 1.63s). 그래서 이 형태가 어디에서도 안 잡혔다.
    """
    from everyric2.server.worker import _impossible_word_distribution

    spans = [(51.7, 55.0), (55.0, 55.02), (55.02, 55.04), (55.04, 55.06)]
    ws = _word_line(["Approved"] * 4, spans)
    assert _impossible_word_distribution(ws, 51.65, 55.72, 11.0) is True


def test_one_swallowed_function_word_does_not_resynthesise_the_line():
    """기능어 하나가 1프레임을 받는 것은 흔하고, 그것 때문에 라인 전체를 버리면 손해다.

    실측 사례: `All in my heart その期待感`에서 `in`이 0.02초. 나머지 글자들의 CTC 분포는
    맞으므로 균등 재합성으로 갈아치우면 오히려 나빠진다. 눌린 글자 비중이 1/3 미만이면
    건드리지 않는다.
    """
    from everyric2.server.worker import _impossible_word_distribution

    words = ["All", "in", "my", "heart", "その期待感"]
    spans = [(0.0, 0.4), (0.4, 0.42), (0.5, 0.9), (0.9, 1.6), (1.6, 3.0)]
    ws = _word_line(words, spans)
    assert _impossible_word_distribution(ws, 0.0, 3.0, 11.0) is False


def test_fast_but_real_singing_is_left_alone():
    """비율(글자/초)로 판정하면 정상 가창을 잡는다 — 그래서 프레임 수로 판정한다.

    실측 오탐: `消去しても` 5글자 / 0.44s = 11.4글자/초로 max_char_rate(11)를 넘지만
    22프레임이라 눌린 것이 아니다. 조밀 음차 후의 `어프룹`×4([0.62, 0.83, 0.41, 0.38])도
    같은 이유로 걸리지 않아야 한다.
    """
    from everyric2.server.worker import _impossible_word_distribution

    ws = _word_line(["消去しても"], [(0.0, 0.44)])
    assert _impossible_word_distribution(ws, 0.0, 0.5, 11.0) is False

    spans = [(0.0, 0.62), (0.62, 1.45), (1.45, 1.86), (1.86, 2.24)]
    ws2 = _word_line(["어프룹"] * 4, spans)
    assert _impossible_word_distribution(ws2, 0.0, 2.24, 11.0) is False


def test_pressed_gate_is_off_when_the_char_rate_gate_is_off():
    # max_char_rate<=0은 이 게이트 전체의 비활성 스위치다 — ④도 함께 꺼져야 한다
    from everyric2.server.worker import _impossible_word_distribution

    spans = [(51.7, 55.0), (55.0, 55.02), (55.02, 55.04), (55.04, 55.06)]
    ws = _word_line(["Approved"] * 4, spans)
    assert _impossible_word_distribution(ws, 51.65, 55.72, 0.0) is False
