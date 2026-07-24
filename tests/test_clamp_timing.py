"""라인 타이밍 세이프가드 회귀 테스트.

`_clamp_stretched_lines`(기존 8s+ & 발성<50% 클램프)에 더해 이번에 추가한 두 규칙:
- `_clamp_repeated_outliers`: 같은 가사 3회 이상 반복 시 형제 중앙값 대비 outlier 클램프
- `_pull_post_interlude_starts`: 긴 간주 뒤 늦게 시작한 라인 start를 첫 발성 리전으로 당김

b2NTglk9tvI(熱異常) 곡에서 (B) 반복 훅 라인 하나만 7초 outlier로 늘어지고,
(C) 33초 간주 뒤 첫 라인 시작이 6.3초 늦게 잡히던 두 붕괴 유형을 각각 방어한다.
"""
import pytest

from everyric2.audio.vad import VADResult, VocalRegion
from everyric2.inference.prompt import SyncResult, WordSegment
from everyric2.server.worker import (
    _clamp_repeated_outliers,
    _clamp_stretched_lines,
    _extend_phrase_final_tails,
    _pull_post_interlude_starts,
)


def _line(text: str, start: float, end: float) -> SyncResult:
    return SyncResult(text=text, start_time=start, end_time=end)


def _wline(text: str, start: float, end: float, w0: float, w1: float) -> SyncResult:
    """글자 스팬이 [w0,w1]에 균등하게 깔린 라인 (라인 경계는 [start,end])."""
    step = (w1 - w0) / len(text)
    ws = [
        WordSegment(word=c, start=w0 + i * step, end=w0 + (i + 1) * step, confidence=0.01)
        for i, c in enumerate(text)
    ]
    return SyncResult(text=text, start_time=start, end_time=end, word_segments=ws)


def _vad(*spans: tuple[float, float]) -> VADResult:
    regions = [VocalRegion(start=s, end=e, energy=0.1) for s, e in spans]
    total = max((e for _, e in spans), default=0.0)
    return VADResult(
        regions=regions,
        sample_rate=16000,
        frame_duration=0.02,
        energy_threshold=0.01,
        total_duration=total,
    )


# --- 규칙 1: 반복행 형제 중앙값 클램프 -------------------------------------


def test_repeated_outlier_is_clamped_to_median():
    # 같은 훅 4번 중 하나만 7초 outlier(나머지는 2초) → 중앙값(2.0) 길이로 잘림
    results = [
        _line("같은 가사", 10.0, 12.0),
        _line("같은 가사", 12.0, 14.0),
        _line("같은 가사", 14.0, 16.0),
        _line("같은 가사", 16.0, 23.0),  # dur 7.0 → outlier
    ]
    clamped: set[int] = set()
    _clamp_repeated_outliers(results, clamped)

    # median([2,2,2,7]) = 2.0, limit = max(2.0*2.5, 4.0) = 5.0, 7.0 > 5.0 → 클램프
    assert clamped == {3}
    assert results[3].end_time == pytest.approx(18.0)  # start(16.0) + median(2.0)
    # 정상 형제는 손대지 않는다
    assert results[0].end_time == pytest.approx(12.0)
    assert results[1].end_time == pytest.approx(14.0)


def test_repeated_key_ignores_whitespace_and_symbols():
    # 공백/괄호/문장부호 차이는 같은 반복행으로 묶여야 outlier가 잡힌다
    results = [
        _line("사랑해!", 0.0, 2.0),
        _line("사랑해 !", 2.0, 4.0),
        _line("(사랑해)", 4.0, 6.0),
        _line("사랑해", 6.0, 13.0),  # dur 7.0 → outlier
    ]
    clamped: set[int] = set()
    _clamp_repeated_outliers(results, clamped)
    assert clamped == {3}
    assert results[3].end_time == pytest.approx(8.0)  # 6.0 + median 2.0


def test_two_siblings_are_not_clamped():
    # 형제가 2개뿐이면 중앙값 통계가 불안정 → 건드리지 않는다
    results = [
        _line("훅", 10.0, 12.0),
        _line("훅", 12.0, 20.0),  # dur 8.0
    ]
    clamped: set[int] = set()
    _clamp_repeated_outliers(results, clamped)
    assert clamped == set()
    assert results[1].end_time == pytest.approx(20.0)


def test_abnormal_median_is_not_clamped():
    # 중앙값 자체가 비정상(<0.5s)이면 배율 판정을 신뢰할 수 없어 건드리지 않는다
    results = [
        _line("짧", 0.0, 0.2),
        _line("짧", 1.0, 1.2),
        _line("짧", 2.0, 10.0),  # dur 8.0 이지만 median 0.2 → 스킵
    ]
    clamped: set[int] = set()
    _clamp_repeated_outliers(results, clamped)
    assert clamped == set()
    assert results[2].end_time == pytest.approx(10.0)


def test_moderate_repeat_within_limit_is_not_clamped():
    # 중앙값의 2.5배·4초 문턱을 둘 다 넘지 않으면 정상 변주로 보고 유지
    results = [
        _line("반복", 0.0, 2.0),
        _line("반복", 2.0, 4.0),
        _line("반복", 4.0, 7.5),  # dur 3.5 < max(5.0, 4.0) → 유지
    ]
    clamped: set[int] = set()
    _clamp_repeated_outliers(results, clamped)
    assert clamped == set()
    assert results[2].end_time == pytest.approx(7.5)


# --- 규칙 1b: 늘어난 라인 클램프 — 라인 정체는 '글자 질량 최대' 리전 ------------


def test_stretched_clamp_keeps_end_when_body_is_in_last_region():
    # OHcNQHbWrFY idx21「あなたはどうして怖くないの」재현: 라인 81.34→119.21이 간주를 덮고
    # 실제 발성(글자 질량)은 마지막 리전(114.5~116.97)에 있다. 첫 리전(간주 초입 잔향)을
    # 라인 정체로 가정하면 맞는 끝(정답 116.97에 근접한 119.21)을 버려 -35.69s가 된다.
    r = _wline("あなたはどうして怖くないの", 81.34, 119.21, 114.5, 116.97)
    vad = _vad((81.0, 83.2), (114.5, 116.97))
    results, clamped = _clamp_stretched_lines([r], vad)

    assert clamped == {0}
    assert results[0].end_time == pytest.approx(117.27)  # 116.97 + 0.3 (잔향 리전 83.5가 아님)


def test_stretched_clamp_uses_first_region_when_body_is_there():
    # 반대 방향 보존: 글자 질량이 첫 리전에 있으면 예전과 동일하게 첫 리전 끝으로 클램프
    r = _wline("あいうえお", 10.0, 30.0, 10.2, 12.0)
    vad = _vad((10.0, 12.0), (26.0, 27.0))
    results, clamped = _clamp_stretched_lines([r], vad)

    assert clamped == {0}
    assert results[0].end_time == pytest.approx(12.3)  # 12.0 + 0.3


def test_stretched_clamp_body_region_survives_zero_length_word_spans():
    # CTC 잔해는 글자 스팬이 길이 0으로 무너진다 — 겹침 길이로는 질량이 전부 0이라
    # 중점 기준으로 세야 뒤쪽 리전을 정체로 찾는다
    ws = [WordSegment(word=c, start=115.5, end=115.5, confidence=0.001) for c in "あいうえ"]
    r = SyncResult(text="あいうえ", start_time=81.0, end_time=119.0, word_segments=ws)
    vad = _vad((81.0, 83.0), (114.5, 116.9))
    results, clamped = _clamp_stretched_lines([r], vad)

    assert clamped == {0}
    assert results[0].end_time == pytest.approx(117.2)  # 116.9 + 0.3


def test_stretched_clamp_falls_back_to_first_region_without_word_mass():
    # 글자가 어느 리전에도 안 떨어지면(전부 무음 위) 질량을 못 재므로 첫 리전 폴백
    ws = [WordSegment(word=c, start=90.0, end=90.0, confidence=0.001) for c in "あいうえ"]
    r = SyncResult(text="あいうえ", start_time=81.0, end_time=119.0, word_segments=ws)
    vad = _vad((81.0, 83.0), (114.5, 116.9))
    results, clamped = _clamp_stretched_lines([r], vad)

    assert clamped == {0}
    assert results[0].end_time == pytest.approx(83.3)  # 83.0 + 0.3


# --- 규칙 2: 간주 후 시작 앵커 당기기 --------------------------------------


def test_post_interlude_start_is_pulled_to_first_region():
    # 15초 간주 뒤 라인 시작(20.0)이 실제 발성(14.0)보다 6초 늦음 → 당겨진다
    results = [
        _line("앞 라인", 0.0, 5.0),
        _line("간주 뒤 첫 라인", 20.0, 24.0),
    ]
    vad = _vad((2.0, 4.0), (14.0, 24.0))
    clamped: set[int] = set()
    _pull_post_interlude_starts(results, vad, clamped)

    # 간주 시작(prev_end 5.0) 이후 첫 리전은 14.0 → start = 14.0 - 0.15
    assert clamped == {1}
    assert results[1].start_time == pytest.approx(13.85)
    assert results[1].end_time == pytest.approx(24.0)  # end는 유지
    assert results[0].start_time == pytest.approx(0.0)  # 앞 라인 불변


def test_pull_skipped_when_new_duration_exceeds_triple():
    # 당긴 결과 duration이 원래(1.0s)의 3배를 넘으면 오탐 방지로 적용하지 않는다
    results = [
        _line("앞", 0.0, 5.0),
        _line("뒤", 30.0, 31.0),  # orig_dur 1.0
    ]
    vad = _vad((14.0, 31.0))  # 당기면 dur 17.15s ≫ 3.0 → 스킵
    clamped: set[int] = set()
    _pull_post_interlude_starts(results, vad, clamped)
    assert clamped == set()
    assert results[1].start_time == pytest.approx(30.0)


def test_pull_skipped_when_gap_is_short():
    # 직전 라인과의 간격이 8초 미만이면 간주가 아니므로 당기지 않는다
    results = [
        _line("앞", 0.0, 5.0),
        _line("뒤", 10.0, 14.0),  # gap 5.0 < 8.0
    ]
    vad = _vad((6.0, 14.0))
    clamped: set[int] = set()
    _pull_post_interlude_starts(results, vad, clamped)
    assert clamped == set()
    assert results[1].start_time == pytest.approx(10.0)


def test_pull_backtracks_vocal_block_chain_not_isolated_echo():
    # 熱異常 실측 재현: 40초 간주 초입의 고립 잔향 리전(132s대)은 무시하고,
    # 라인과 겹치는 리전에서 ≤2s 간격으로 이어지는 가창 블록 시작(165.48)으로 당긴다
    results = [
        _line("간주 앞", 129.0, 131.8),
        _line("간주 뒤 첫 라인", 171.8, 187.2),  # 실제 가창은 165.5부터
    ]
    vad = _vad(
        (132.2, 132.8),   # 고립 잔향 — 다음 리전과 32.7s 간격이라 체인 밖
        (165.5, 169.6),   # 가창 블록 시작
        (171.0, 174.5),   # 라인과 겹치는 첫 리전 (1.4s 간격으로 앞 리전과 연결)
        (176.0, 180.2),
    )
    clamped: set[int] = set()
    _pull_post_interlude_starts(results, vad, clamped)

    assert clamped == {1}
    assert results[1].start_time == pytest.approx(165.35)  # 165.5 - 0.15
    assert results[1].end_time == pytest.approx(187.2)


def test_pull_skipped_when_region_not_early_enough():
    # 첫 발성 리전이 라인 start보다 1.5초 이상 이르지 않으면 당길 필요 없음
    results = [
        _line("앞", 0.0, 5.0),
        _line("뒤", 20.0, 24.0),
    ]
    vad = _vad((19.0, 24.0))  # 19.0 > 20.0 - 1.5 = 18.5 → 스킵
    clamped: set[int] = set()
    _pull_post_interlude_starts(results, vad, clamped)
    assert clamped == set()
    assert results[1].start_time == pytest.approx(20.0)


def test_pull_skipped_when_first_char_sits_on_vocal_onset():
    # G5hScSFkib4 idx6/idx27 재현: raw 시작이 이미 정확(+0.37/+0.27)한데 앞선 리전 체인으로
    # -3.30s/-6.91s 끌려갔다. 첫 글자가 실제 발성 온셋 위(±0.5s)면 당기지 않는다.
    results = [
        _line("앞", 0.0, 5.0),
        _wline("あいうえ", 20.0, 24.0, 20.0, 23.6),  # 첫 글자가 리전 온셋 20.0에 정확히 얹힘
    ]
    vad = _vad((2.0, 4.0), (16.3, 18.0), (20.0, 24.0))  # 체인상 16.3까지 역추적 가능
    clamped: set[int] = set()
    _pull_post_interlude_starts(results, vad, clamped)

    assert clamped == set()
    assert results[1].start_time == pytest.approx(20.0)
    assert results[1].word_segments[0].start == pytest.approx(20.0)  # 글자도 불변


def test_pull_fires_for_mid_region_first_char_and_shifts_word_spans():
    # 熱異常 재현(진짜 늦게 잡힌 라인): 첫 글자(171.8)가 리전(171.0~174.5) 한복판이라
    # 온셋 가드에 안 걸리고 가창 블록 시작(165.5)으로 당겨진다. 이때 글자 스팬도 함께 이동
    # — start만 옮기면 라인 시작과 글자 위치가 어긋나 이후 단계가 '글자가 라인 밖'으로 오판한다.
    results = [
        _line("간주 앞", 129.0, 131.8),
        _wline("間奏後の歌", 171.8, 187.2, 171.8, 187.2),
    ]
    vad = _vad((132.2, 132.8), (165.5, 169.6), (171.0, 174.5), (176.0, 180.2))
    clamped: set[int] = set()
    _pull_post_interlude_starts(results, vad, clamped)

    assert clamped == {1}
    assert results[1].start_time == pytest.approx(165.35)
    ws = results[1].word_segments
    assert ws[0].start == pytest.approx(165.35)  # 글자 스팬이 라인과 함께 이동
    assert ws[-1].end == pytest.approx(187.2)
    assert all(ws[i].end <= ws[i + 1].start + 1e-9 for i in range(len(ws) - 1))  # 단조 유지


def test_pull_onset_guard_inactive_without_word_segments():
    # 글자 타이밍이 없으면 판정 근거가 없다 — 라인 start는 이미 보정 대상 값이라 못 쓴다.
    # (기존 동작 보존: word_segments 없는 라인은 예전처럼 당겨진다)
    results = [_line("앞", 0.0, 5.0), _line("뒤", 20.0, 24.0)]
    vad = _vad((14.0, 24.0))
    clamped: set[int] = set()
    _pull_post_interlude_starts(results, vad, clamped)
    assert clamped == {1}
    assert results[1].start_time == pytest.approx(13.85)


# --- 규칙 3: 소절 끝 늘임음 연장 --------------------------------------------


def test_phrase_final_tail_extended_to_vad_end():
    # 소절 끝 라인(뒤 갭 1.5초)이 VAD 리전 안에서 0.6초 일찍 끝남 → 리전 끝까지 연장
    results = [
        SyncResult(
            text="늘임음 라인", start_time=10.0, end_time=12.0,
            word_segments=[WordSegment("늘", 10.0, 11.0), WordSegment("임", 11.0, 12.0)],
        ),
        SyncResult(text="다음 라인", start_time=13.5, end_time=15.0),
    ]
    vad = _vad((9.8, 12.6), (13.4, 15.2))
    clamped: set[int] = set()
    _extend_phrase_final_tails(results, vad, clamped)

    assert results[0].end_time == pytest.approx(12.6)  # 리전 끝
    assert results[0].word_segments[-1].end == pytest.approx(12.6)  # 마지막 글자도 함께
    assert results[0].word_segments[0].end == pytest.approx(11.0)  # 앞 글자는 불변


def test_butted_line_is_not_extended():
    # 다음 라인이 0.3초 이내로 붙어 있으면(소절 중간) 건드리지 않는다
    results = [
        SyncResult(text="중간 라인", start_time=10.0, end_time=12.0),
        SyncResult(text="바로 다음", start_time=12.1, end_time=14.0),
    ]
    vad = _vad((9.8, 14.2))
    clamped: set[int] = set()
    _extend_phrase_final_tails(results, vad, clamped)
    assert results[0].end_time == pytest.approx(12.0)


def test_tail_extension_capped_and_bounded_by_next_start():
    # 리전 꼬리가 3초 초과(병합 의심)면 +1.5초 캡, 다음 라인 시작 -0.05초를 넘지 않는다
    results = [
        SyncResult(text="캡 라인", start_time=10.0, end_time=12.0),
        SyncResult(text="다음", start_time=13.0, end_time=15.0),
    ]
    vad = _vad((9.8, 30.0))
    clamped: set[int] = set()
    _extend_phrase_final_tails(results, vad, clamped)
    assert results[0].end_time == pytest.approx(12.95)  # min(30.0, 13.0-0.05, 12.0+1.5)

    # 마지막 라인: 다음 라인이 없으면 캡(+1.5)까지
    solo = [SyncResult(text="마지막", start_time=40.0, end_time=41.0)]
    _extend_phrase_final_tails(solo, _vad((39.5, 60.0)), set())
    assert solo[0].end_time == pytest.approx(42.5)


def test_tail_cap_widens_for_short_region_tail():
    # 커버 실측 잔존 케이스 재현: 리전 꼬리 2.0초(≤3.0) → 캡 2.5초로 리전 끝까지 연장
    results = [
        SyncResult(text="사비 늘임음", start_time=166.0, end_time=167.9),
        SyncResult(text="다음 소절", start_time=170.9, end_time=173.0),
    ]
    vad = _vad((165.5, 169.9))
    _extend_phrase_final_tails(results, vad, set())
    assert results[0].end_time == pytest.approx(169.9)  # 1.5캡(169.4)을 넘어 리전 끝까지


def test_tail_cap_conservative_before_interlude():
    # 커버 cue#37 재현: 리전 꼬리 2.3초(≤3)지만 다음 라인까지 22초 간주 → 보수 캡 +1.5초
    # (간주 직전은 리전 끝이 잔향으로 늦게 잡히고 재실행 간 흔들려 꼬리 길이를 못 믿는다)
    results = [
        SyncResult(text="간주 직전 늘임음", start_time=66.0, end_time=67.3),
        SyncResult(text="간주 뒤", start_time=89.5, end_time=92.0),
    ]
    vad = _vad((60.0, 69.6))
    _extend_phrase_final_tails(results, vad, set())
    assert results[0].end_time == pytest.approx(68.8)  # 67.3 + 1.5 (2.5캡이면 69.6까지 갔을 것)


def test_tail_cap_stays_conservative_for_merged_region():
    # 간주를 삼킨 병합 리전(꼬리 21.8초 > 3.0) → +1.5초 캡 유지로 과연장 방지
    results = [
        SyncResult(text="간주 앞 라인", start_time=66.0, end_time=67.0),
        SyncResult(text="간주 뒤", start_time=89.0, end_time=92.0),
    ]
    vad = _vad((60.0, 88.8))
    _extend_phrase_final_tails(results, vad, set())
    assert results[0].end_time == pytest.approx(68.5)  # 67.0 + 1.5


def test_tail_extension_skipped_outside_vad():
    # 라인 끝이 발성 리전 밖(이미 리전 끝을 지나침)이면 따라갈 꼬리가 없다 → 유지
    results = [SyncResult(text="밖 라인", start_time=10.0, end_time=12.0)]
    vad = _vad((9.8, 11.5))
    _extend_phrase_final_tails(results, vad, set())
    assert results[0].end_time == pytest.approx(12.0)


def test_clamped_line_is_not_re_extended():
    # 반복행 클램프로 잘라낸 라인은 늘임음 연장이 도로 늘리면 안 된다
    results = [
        _line("훅", 10.0, 12.0),
        _line("훅", 12.0, 14.0),
        _line("훅", 14.0, 21.0),  # outlier → 16.0으로 클램프될 라인
        _line("한참 뒤", 40.0, 44.0),
    ]
    vad = _vad((10.0, 21.0), (39.8, 44.0))
    results, clamped = _clamp_stretched_lines(results, vad)
    assert 2 in clamped
    assert results[2].end_time == pytest.approx(16.0)  # 연장 없이 클램프 값 유지


def test_fixes_labels_recorded_per_rule():
    # fixes를 넘기면 어떤 규칙이 어떤 라인을 고쳤는지 라벨링된다 (디버그 오버레이용)
    results = [
        _line("훅", 10.0, 12.0),
        _line("훅", 12.0, 14.0),
        _line("훅", 14.0, 21.0),  # 반복 outlier → repeat
        _line("늘임음", 25.0, 26.0),  # phrase-final, 리전 끝 27.0까지 연장 → tail
        _line("간주 뒤", 45.0, 49.0),  # 간주 후 당김 → pull (clamped 마킹 → tail 제외)
    ]
    vad = _vad((10.0, 21.0), (24.8, 27.0), (39.0, 49.0))
    fixes: dict[int, list[str]] = {}
    results, clamped = _clamp_stretched_lines(results, vad, fixes=fixes)

    assert fixes[2] == ["repeat"]
    assert fixes[3] == ["tail"]
    assert fixes[4] == ["pull"]
    assert 0 not in fixes and 1 not in fixes  # 안 고친 라인은 라벨 없음


# --- 통합: 기존 규칙 보존 + 새 규칙 공존 -----------------------------------


def test_existing_stretched_clamp_still_applies():
    # 기존 규칙(8s+ & 발성<50%): 10초 라인의 발성이 앞 2초뿐 → 첫 리전 끝+0.3으로 클램프
    results = [_line("긴 라인", 0.0, 10.0)]
    vad = _vad((0.0, 2.0))
    results, clamped = _clamp_stretched_lines(results, vad)
    assert clamped == {0}
    # new_end = min(10.0, max(2.0 + 0.3, 0.0 + 1.5)) = 2.3
    assert results[0].end_time == pytest.approx(2.3)


def test_new_rules_coexist_via_public_entry():
    # 반복 outlier + 간주 후 늦은 시작이 한 시퀀스에 함께 있어도 각각 보정된다
    results = [
        _line("훅", 10.0, 12.0),
        _line("훅", 12.0, 14.0),
        _line("훅", 14.0, 21.0),  # 반복 outlier (dur 7.0)
        _line("간주 뒤", 40.0, 44.0),  # 앞 라인 end(21.0)와 gap 19.0 → 간주
    ]
    vad = _vad((10.0, 21.0), (34.0, 44.0))
    results, clamped = _clamp_stretched_lines(results, vad)

    assert 2 in clamped  # 반복 outlier 클램프
    assert results[2].end_time == pytest.approx(16.0)  # start 14.0 + median 2.0
    assert 3 in clamped  # 간주 후 시작 당김
    assert results[3].start_time == pytest.approx(33.85)  # 34.0 - 0.15
