"""ko/ja 융합(라인 내부 원문 글자 타이밍 교체) 회귀 테스트.

독음(ko) 경로에서 원문 글자 스팬은 오디오를 한 번도 만지지 않는다 — '정렬된 한글 음절 →
모라 → 원문 글자'의 3단 역매핑 합성물이라 라인 경계가 완벽해도 라인 **내부** 분포가 뭉친다
(실측: ko 정렬 곡은 원문 글자의 3자 이상 동시 시작이 38~59%, ja 정렬 곡은 2%).

융합은 승자 선택이 아니다: 검증된 ko 라인 경계·pron_segments는 그대로 두고, 라인 내부
글자 분포만 ja 정렬의 실측값으로 교체한다. 여기서 고정하는 계약:
  - ko 라인 경계(start_time/end_time)와 라인 conf·텍스트는 불변,
  - pron_segments 불변(융합은 word_segments만 만진다),
  - 직렬화 계약 join(words)==text와 단조 비감소 유지,
  - ja가 그 라인에서 붕괴했거나 본문 글자를 덜 덮으면 융합하지 않고 역매핑 유지,
  - 스위치를 끄면(= ja 정렬 자체를 안 돌림) 완전 무동작.

사상 **목표 구간**은 라인 경계가 아니라 그 라인의 발음 음절 구간이다. ko 라인의 끝은 실제
발성 종료보다 늦은 경우가 많아(끝음 연장 tail, 다음 줄까지의 여백) 라인 경계에 사상하면
원문 글자만 그 여백까지 늘어나고, 실측 시각 그대로인 발음 음절보다 뒤로 밀린다 — 실측
6곡 전부 "원문 − 발음" 분위 차이가 양수(첫 글자 0, 75% +0.09~+0.44, 끝 +0.09~+0.39)로
뒤로 갈수록 벌어졌다. 아래 ``_pron_window`` 계열 테스트가 그 지표를 직접 잰다.
"""
from everyric2.config.settings import AlignmentSettings
from everyric2.inference.prompt import SyncResult, WordSegment
from everyric2.server.worker import (
    _dual_align_should_run,
    _full_coverage_words,
    _fuse_original_char_timing,
    _measured_anchor_count,
    _measured_vocal_window,
    _original_align_needed,
    _synthesize_collapsed_timing,
)


def _ws(word, start, end, conf=None):
    return WordSegment(word=word, start=start, end=end, confidence=conf)


def _clumped(text, start, end, conf=0.001):
    """역매핑 실패 양상 — 글자가 전부 라인 선두에 뭉친 ko 라인."""
    return SyncResult(
        text=text,
        start_time=start,
        end_time=end,
        confidence=conf,
        word_segments=[_ws(c, start, start + 0.01, conf) for c in text],
    )


def _spread(text, start, end, conf=0.02):
    """ja 정렬 양상 — 글자가 라인 폭에 고르게 퍼진 실측 분포."""
    n = len(text)
    span = (end - start) / n
    return SyncResult(
        text=text,
        start_time=start,
        end_time=end,
        confidence=conf,
        word_segments=[
            _ws(c, start + span * k, start + span * (k + 1), conf) for k, c in enumerate(text)
        ],
    )


def _starts(ws):
    return [w.start for w in ws]


# 실측 라인(중앙값 3.54s)에 끝음 연장 tail 0.40s가 붙은 형태 — 발성은 VOCAL_END에서 끝나는데
# 라인 경계는 LINE_END까지 늘어나 있다. 이 여백이 원문 글자를 뒤로 밀던 구간이다.
LINE_START, VOCAL_END, LINE_END = 10.0, 13.14, 13.54


def test_fusion_skips_lines_where_ja_disagrees_with_the_backmapping():
    """ja 실측 «모양»이 ko 역매핑과 중앙값 0.35s 넘게 어긋나는 라인은 융합하지 않는다.

    선형 사상이 전역 이동·스케일은 지워 주므로, 살아남는 불일치는 라인 안 분포의 모양
    차이다 — 그것이 컸던 줄들이 사용자 청취에서 「한글 전사가 더 정확한」 줄들이었다
    (JW3N-HvU0MA 융합 25줄 중 8줄 >0.35s, p90 0.76s). 그 줄은 뭉치더라도 ko 실측에
    정박한 역매핑을 지킨다.
    """
    text = "가나다라마바"
    ko = _spread(text, 10.0, 16.0)  # 역매핑이 고르게 퍼진 기준 분포
    # ja: 앞 다섯 글자가 선두 0.5s에 뭉치고 마지막 글자만 끝에 — 비선형 모양 차이
    ja = SyncResult(
        text=text,
        start_time=10.0,
        end_time=16.0,
        confidence=0.02,
        word_segments=[_ws(c, 10.0 + 0.1 * k, 10.1 + 0.1 * k, 0.02) for k, c in enumerate(text[:5])]
        + [_ws(text[5], 15.9, 16.0, 0.02)],
    )
    before = _starts(ko.word_segments)
    fused = _fuse_original_char_timing([ko], [ja], {}, max_disagreement=0.35)
    assert fused == set()
    assert _starts(ko.word_segments) == before  # 역매핑 그대로

    # 모양이 거의 같으면(±0.1s) 세밀함이 이득이라 융합된다
    ko2 = _spread(text, 10.0, 16.0)
    ja2 = _spread(text, 10.1, 16.1)
    fused2 = _fuse_original_char_timing([ko2], [ja2], {}, max_disagreement=0.35)
    assert fused2 == {0}


def _pron(n, start, end, text="가"):
    """발음 음절 스팬 n개를 [start,end]에 균등 배치 — ko CTC 실측값 자리."""
    w = (end - start) / n
    return [
        {"text": text, "start": start + w * k, "end": start + w * (k + 1), "resolved": True}
        for k in range(n)
    ]


def _quantile(xs, p):
    """분위 시각 — 팀 리드 실측 표와 같은 지표(선형 보간)."""
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    pos = p * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


# ---- 융합 본체: ko 경계 유지 + 내부만 ja 실측 분포 -----------------------------


def test_fuse_replaces_intra_line_distribution_keeping_ko_bounds():
    # ko는 라인 [10,20]에 글자를 선두로 뭉쳤고, ja는 [100,110]에서 고르게 쟀다.
    # 융합 후: 라인 경계는 ko 그대로, 내부 분포는 ja의 상대 분포.
    ko = [_clumped("あいうえお", 10.0, 20.0)]
    ja = [_spread("あいうえお", 100.0, 110.0)]
    fixes: dict[int, list[str]] = {}

    fused = _fuse_original_char_timing(ko, ja, fixes, max_char_rate=11.0)

    assert fused == {0}
    r = ko[0]
    assert (r.start_time, r.end_time) == (10.0, 20.0)  # ko 라인 경계 불변
    ws = r.word_segments
    assert abs(ws[0].start - 10.0) < 1e-9 and abs(ws[-1].end - 20.0) < 1e-9
    assert _starts(ws) == sorted(_starts(ws))  # 단조
    # ja가 균등이었으니 사상 결과도 균등 2.0s — 뭉침이 사라졌다
    assert all(abs((w.end - w.start) - 2.0) < 1e-9 for w in ws)
    # 뭉침 지표: 같은 시각에 시작하는 글자가 더는 없다
    assert len(set(_starts(ws))) == len(ws)


def test_fuse_keeps_line_text_and_confidence_and_inherits_ja_word_conf():
    ko = [_clumped("あいうえお", 10.0, 20.0, conf=0.0004)]
    ja = [_spread("あいうえお", 0.0, 5.0, conf=0.03)]
    _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0)
    r = ko[0]
    assert r.text == "あいうえお"
    assert r.confidence == 0.0004  # 라인 conf(quality_score 입력)는 ko 값 유지
    assert all(w.confidence == 0.03 for w in r.word_segments)  # 글자 conf는 ja 실측


def test_fuse_labels_lines_in_fixes():
    ko = [_clumped("あいうえお", 10.0, 20.0), _clumped("かきくけこ", 20.0, 30.0)]
    ja = [_spread("あいうえお", 0.0, 5.0), _spread("かきくけこ", 5.0, 10.0)]
    fixes: dict[int, list[str]] = {1: ["snap"]}
    _fuse_original_char_timing(ko, ja, fixes, max_char_rate=11.0)
    assert fixes[0] == ["fuse"]
    assert fixes[1] == ["snap", "fuse"]  # 기존 라벨 뒤에 덧붙임


def test_fuse_does_not_mutate_ja_results():
    # 누출 스플라이스는 ja의 word_segments 객체를 results에 그대로 꽂아둔다 —
    # 융합이 제자리 수정하면 공유 객체를 흔든다.
    ko = [_clumped("あいうえお", 10.0, 20.0)]
    ja = [_spread("あいうえお", 100.0, 110.0)]
    before = [(w.word, w.start, w.end) for w in ja[0].word_segments]
    _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0)
    assert [(w.word, w.start, w.end) for w in ja[0].word_segments] == before
    assert ko[0].word_segments is not ja[0].word_segments


def test_fuse_pron_segments_untouched():
    # pron_segments는 별도 자료구조(pron_data)이고 융합은 word_segments만 만진다.
    ko = [_clumped("あいうえお", 10.0, 20.0)]
    ja = [_spread("あいうえお", 0.0, 5.0)]
    pron_data = {0: {"pron_segments": [{"text": "아", "start": 10.0, "end": 12.0}]}}
    snapshot = [dict(s) for s in pron_data[0]["pron_segments"]]
    _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0)
    assert pron_data[0]["pron_segments"] == snapshot


# ---- 융합 건너뛰기: ja가 못 믿을 때는 역매핑 유지 -------------------------------


def test_fuse_skips_line_where_ja_collapsed():
    # ja가 그 라인에서 글자를 한 구석에 욱여넣은 잔해 → _impossible_word_distribution
    ko = [_clumped("あいうえお", 10.0, 20.0)]
    crammed = SyncResult(
        text="あいうえお",
        start_time=0.0,
        end_time=10.0,
        word_segments=[_ws(c, 0.0 + k * 0.01, 0.0 + (k + 1) * 0.01, 0.02) for k, c in enumerate("あいうえお")],
    )
    before = [(w.start, w.end) for w in ko[0].word_segments]
    fixes: dict[int, list[str]] = {}
    assert _fuse_original_char_timing(ko, [crammed], fixes, max_char_rate=11.0) == set()
    assert [(w.start, w.end) for w in ko[0].word_segments] == before
    assert fixes == {}


def test_fuse_skips_line_where_ja_words_escape_its_bounds():
    # ja word가 자기 라인 밖을 크게 가리키면(경계 이탈) 그 라인 정렬은 잔해다
    ko = [_clumped("あいうえお", 10.0, 20.0)]
    escaped = SyncResult(
        text="あいうえお",
        start_time=0.0,
        end_time=5.0,
        word_segments=[_ws(c, 20.0 + k, 21.0 + k, 0.02) for k, c in enumerate("あいうえお")],
    )
    assert _fuse_original_char_timing(ko, [escaped], {}, max_char_rate=11.0) == set()


def test_fuse_skips_when_ja_yields_fewer_anchors_than_backmapping():
    # ja가 한자를 OOV로 흘려 토큰이 두 개만 남으면 융합은 해상도를 떨어뜨린다 →
    # 역매핑(글자마다 서로 다른 시각)을 유지한다.
    text = "夜に駆ける"
    ko = [
        SyncResult(
            text=text,
            start_time=10.0,
            end_time=20.0,
            word_segments=[_ws(c, 10.0 + k, 11.0 + k, 0.001) for k, c in enumerate(text)],
        )
    ]
    ja = [
        SyncResult(
            text=text,
            start_time=0.0,
            end_time=5.0,
            word_segments=[_ws("に", 1.0, 2.0, 0.02), _ws("る", 4.0, 5.0, 0.02)],
        )
    ]
    assert _measured_anchor_count(text, ja[0].word_segments) == 2
    assert _measured_anchor_count(text, ko[0].word_segments) == 5
    assert _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0) == set()


def test_fuse_applies_to_full_coverage_ko_line_with_one_anchor():
    # 융합이 노리는 바로 그 라인: 역매핑이 한 음절 스팬을 다섯 글자에 복사해 커버리지는
    # 100%인데 앵커는 1개다. 글자 커버리지로 재면 여기서 정확히 반대로 걸러진다.
    text = "夜に駆ける"
    ko = [_clumped(text, 10.0, 20.0)]  # 다섯 글자 모두 start=10.0
    ja = [_spread(text, 0.0, 5.0)]
    assert _measured_anchor_count(text, ko[0].word_segments) == 1
    assert _measured_anchor_count(text, ja[0].word_segments) == 5
    assert _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0) == {0}


def test_fuse_gives_word_timing_to_line_where_backmapping_produced_none():
    # 커버리지 0.9 게이트를 통과해도 최대 10%의 라인은 발음 표기가 없어 역매핑이 통째로
    # 실패한다(word_segments=None → 글자 타이밍이 아예 없음). 융합이 그 라인을 채운다.
    text = "あいうえお"
    ko = [SyncResult(text=text, start_time=10.0, end_time=20.0, word_segments=None)]
    ja = [_spread(text, 0.0, 5.0)]
    assert _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0) == {0}
    ws = ko[0].word_segments
    assert [w.word for w in ws] == list(text)
    assert abs(ws[0].start - 10.0) < 1e-9 and abs(ws[-1].end - 20.0) < 1e-9


def test_fuse_skips_line_with_mismatched_text():
    # 인덱스가 어긋난 ja를 그대로 실으면 다른 라인의 타이밍이 들어온다 — 방어
    ko = [_clumped("あいうえお", 10.0, 20.0)]
    ja = [_spread("かきくけこ", 0.0, 5.0)]
    assert _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0) == set()


def test_fuse_skips_degenerate_spans():
    ko = [_clumped("あいうえお", 10.0, 10.0)]  # 폭 0 라인
    ja = [_spread("あいうえお", 0.0, 5.0)]
    assert _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0) == set()

    ko2 = [_clumped("あいうえお", 10.0, 20.0)]
    zero = SyncResult(
        text="あいうえお",
        start_time=0.0,
        end_time=5.0,
        word_segments=[_ws(c, 2.0, 2.0, 0.02) for c in "あいうえお"],  # extent 0
    )
    assert _fuse_original_char_timing(ko2, [zero], {}, max_char_rate=0.0) == set()


def test_fuse_forces_monotonic_output_on_nonmonotonic_ja():
    ko = [_clumped("あいうえお", 10.0, 20.0)]
    jumbled = SyncResult(
        text="あいうえお",
        start_time=0.0,
        end_time=10.0,
        word_segments=[
            _ws("あ", 0.0, 2.0, 0.02),
            _ws("い", 6.0, 8.0, 0.02),
            _ws("う", 3.0, 4.0, 0.02),  # 역행
            _ws("え", 8.0, 9.0, 0.02),
            _ws("お", 9.0, 10.0, 0.02),
        ],
    )
    assert _fuse_original_char_timing(ko, [jumbled], {}, max_char_rate=0.0) == {0}
    ws = ko[0].word_segments
    assert _starts(ws) == sorted(_starts(ws))
    assert all(ws[i].end <= ws[i + 1].start + 1e-9 for i in range(len(ws) - 1))
    assert ws[0].start >= 10.0 - 1e-9 and ws[-1].end <= 20.0 + 1e-9


# ---- 스위치 OFF / ja 없음: 완전 무동작 ------------------------------------------


def test_fuse_noop_without_ja_alignment():
    # 스위치가 꺼져 있으면 ja 정렬 자체를 안 돌리므로 ja_results가 None이다
    ko = [_clumped("あいうえお", 10.0, 20.0)]
    before = [(w.word, w.start, w.end, w.confidence) for w in ko[0].word_segments]
    fixes: dict[int, list[str]] = {}
    assert _fuse_original_char_timing(ko, None, fixes, max_char_rate=11.0) == set()
    assert [(w.word, w.start, w.end, w.confidence) for w in ko[0].word_segments] == before
    assert fixes == {}


def test_fuse_noop_on_length_mismatch():
    ko = [_clumped("あいうえお", 10.0, 20.0), _clumped("かきくけこ", 20.0, 30.0)]
    ja = [_spread("あいうえお", 0.0, 5.0)]
    assert _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0) == set()


def test_original_align_needed_gate():
    # 융합 ON → ko 신뢰도와 무관하게 ja 1패스 (융합의 입력이므로 상시 필요)
    assert _original_align_needed(0.0076, 0.002, True) is True
    assert _original_align_needed(None, 0.002, True) is True
    # 융합 OFF → 기존 이중정렬 게이트와 완전히 동일
    for conf in (None, 0.0005, 0.0076):
        assert _original_align_needed(conf, 0.002, False) is _dual_align_should_run(conf, 0.002)


def test_fuse_switch_defaults_on_and_is_overridable(monkeypatch):
    assert AlignmentSettings().fuse_original_chars is True
    monkeypatch.setenv("EVERYRIC_ALIGNMENT_FUSE_ORIGINAL_CHARS", "false")
    assert AlignmentSettings().fuse_original_chars is False


# ---- 직렬화 계약: join(words)==text, 단조 ---------------------------------------


def test_fused_line_serializes_to_exact_text_and_monotonic():
    # ja 토큰은 정규화 텍스트 기준이라 본문의 공백·부호를 안 덮는다 —
    # _full_coverage_words가 그래도 본문을 정확히 재구성하는지(계약) 확인
    text = "ねぇ、 いつか"
    ko = [_clumped(text, 10.0, 20.0)]
    ja = [
        SyncResult(
            text=text,
            start_time=0.0,
            end_time=10.0,
            word_segments=[
                _ws("ね", 0.0, 2.0, 0.02),
                _ws("ぇ", 2.0, 4.0, 0.02),
                _ws("いつ", 6.0, 8.0, 0.02),
                _ws("か", 8.0, 10.0, 0.02),
            ],
        )
    ]
    assert _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0) == {0}
    r = ko[0]
    out = _full_coverage_words(r.text, r.word_segments, r.start_time, r.end_time)
    assert "".join(w["word"] for w in out) == text
    starts = [w["start"] for w in out]
    assert starts == sorted(starts)
    assert all(out[i]["end"] <= out[i + 1]["start"] + 1e-9 for i in range(len(out) - 1))
    assert out[0]["start"] >= 10.0 - 1e-9 and out[-1]["end"] <= 20.0 + 1e-9


def test_fusion_then_resynthesis_pipeline_keeps_contracts():
    # 실제 호출 순서: 융합 → _synthesize_collapsed_timing → 직렬화.
    # leak 라인은 재합성이 그대로 덮고(기존 규칙 유지), 나머지는 융합 분포가 살아남는다.
    text_a, text_b = "あいうえお", "かきくけこ"
    ko = [_clumped(text_a, 10.0, 20.0), _clumped(text_b, 20.0, 30.0)]
    ja = [_spread(text_a, 0.0, 5.0), _spread(text_b, 5.0, 10.0)]
    fixes: dict[int, list[str]] = {1: ["leak"]}
    pron_data = {
        0: {"pron_segments": [{"text": "아", "start": 10.0, "end": 12.0}]},
        1: {"pron_segments": [{"text": "카", "start": 20.0, "end": 22.0}]},
    }

    assert _fuse_original_char_timing(ko, ja, fixes, max_char_rate=11.0) == {0, 1}
    synth = _synthesize_collapsed_timing(
        ko, pron_data, fixes, song_conf=None, threshold=0.0, max_char_rate=11.0
    )
    assert synth == {1}  # leak 라인만 균등 재합성

    for r in ko:
        assert r.start_time in (10.0, 20.0) and r.end_time in (20.0, 30.0)  # 경계 불변
        out = _full_coverage_words(r.text, r.word_segments, r.start_time, r.end_time)
        assert "".join(w["word"] for w in out) == r.text
        starts = [w["start"] for w in out]
        assert starts == sorted(starts)
    # 융합 라인은 재합성 대상이 아니므로 conf가 살아 있다(재합성은 conf를 버린다)
    assert all(w.confidence is not None for w in ko[0].word_segments)
    assert all(w.confidence is None for w in ko[1].word_segments)


def test_fusion_prevents_uniform_resynthesis_of_a_crammed_ko_line():
    # 융합이 재합성보다 먼저 도는 이유: ko 뭉침 라인은 구조 게이트에 걸려 균등 분배로
    # 덮이던 자리인데, ja 실측 분포로 갈아끼우면 그 게이트를 통과해 실측값이 남는다.
    text = "あいうえおかきくけこ"
    ko_only = [_clumped(text, 10.0, 20.0)]
    assert _synthesize_collapsed_timing(
        ko_only, None, {}, song_conf=None, threshold=0.0, max_char_rate=11.0
    ) == {0}  # 융합 없으면 균등 재합성 대상
    assert all(w.confidence is None for w in ko_only[0].word_segments)

    ko = [_clumped(text, 10.0, 20.0)]
    ja = [_spread(text, 0.0, 10.0)]
    fixes: dict[int, list[str]] = {}
    assert _fuse_original_char_timing(ko, ja, fixes, max_char_rate=11.0) == {0}
    assert _synthesize_collapsed_timing(
        ko, None, fixes, song_conf=None, threshold=0.0, max_char_rate=11.0
    ) == set()
    assert all(w.confidence is not None for w in ko[0].word_segments)


# ---- 사상 목표 구간: 라인 경계가 아니라 발음 음절 구간 ---------------------------


def test_fuse_ends_chars_at_pron_window_not_at_the_padded_line_end():
    # 라인 끝 0.40s는 발성 종료 이후 여백이다. 원문 마지막 글자는 라인 끝(13.54)이 아니라
    # 발음 마지막 음절 끝(13.14)에 맞아야 한다 — 수정 전에는 13.54였다.
    text = "あいうえおかきくけこ"
    ko = [_clumped(text, LINE_START, LINE_END)]
    ja = [_spread(text, 100.0, 103.14)]
    pron_data = {0: {"pron_segments": _pron(len(text), LINE_START, VOCAL_END)}}

    assert _fuse_original_char_timing(
        ko, ja, {}, max_char_rate=11.0, pron_data=pron_data
    ) == {0}
    ws = ko[0].word_segments
    assert abs(ws[0].start - LINE_START) < 1e-9
    assert abs(ws[-1].end - VOCAL_END) < 1e-9
    assert ws[-1].end < LINE_END - 0.3  # 여백까지 늘어나지 않았다


def test_fuse_char_quantiles_match_pron_syllable_quantiles():
    # 이번 수정의 핵심 지표: 같은 라인 안에서 (원문 글자 분위 시각 − 발음 음절 분위 시각).
    # 수정 전에는 첫 글자 0, 25% +0.09, 50% +0.18, 75% +0.27, 끝 +0.36으로 뒤로 갈수록
    # 벌어졌다(라인 끝 여백까지 늘리는 선형 사상의 지문). 수정 후에는 전 분위에서 0이다.
    text = "あいうえおかきくけこ"
    ko = [_clumped(text, LINE_START, LINE_END)]
    ja = [_spread(text, 100.0, 103.14)]
    pron_segments = _pron(len(text), LINE_START, VOCAL_END)
    pron_data = {0: {"pron_segments": pron_segments}}

    _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0, pron_data=pron_data)

    char_starts = _starts(ko[0].word_segments)
    pron_starts = [s["start"] for s in pron_segments]
    for p in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert abs(_quantile(char_starts, p) - _quantile(pron_starts, p)) < 1e-9


def test_fuse_keeps_line_bounds_and_serialization_contracts_on_pron_window():
    # 목표 구간이 좁아져도 라인 경계는 불변이고, 직렬화 계약(join==text, 단조, 경계 내
    # 포함)은 그대로다. 본문에 ja 토큰이 안 덮는 공백·부호를 섞어 재구성까지 확인한다.
    text = "ねぇ、 いつか"
    ko = [_clumped(text, LINE_START, LINE_END)]
    ja = [
        SyncResult(
            text=text,
            start_time=0.0,
            end_time=10.0,
            word_segments=[
                _ws("ね", 0.0, 2.0, 0.02),
                _ws("ぇ", 2.0, 4.0, 0.02),
                _ws("いつ", 6.0, 8.0, 0.02),
                _ws("か", 8.0, 10.0, 0.02),
            ],
        )
    ]
    pron_data = {0: {"pron_segments": _pron(5, LINE_START, VOCAL_END)}}

    assert _fuse_original_char_timing(
        ko, ja, {}, max_char_rate=11.0, pron_data=pron_data
    ) == {0}
    r = ko[0]
    assert (r.start_time, r.end_time) == (LINE_START, LINE_END)  # 라인 경계 불변
    assert r.text == text
    out = _full_coverage_words(r.text, r.word_segments, r.start_time, r.end_time)
    assert "".join(w["word"] for w in out) == text
    starts = [w["start"] for w in out]
    assert starts == sorted(starts)
    assert all(out[i]["end"] <= out[i + 1]["start"] + 1e-9 for i in range(len(out) - 1))
    assert out[0]["start"] >= LINE_START - 1e-9 and out[-1]["end"] <= LINE_END + 1e-9
    # 토큰이 덮는 마지막 글자는 발음 구간 끝에서 멈춘다 (뒤 여백으로 안 늘어남)
    assert abs(out[-1]["end"] - VOCAL_END) < 1e-9


def test_fuse_pron_window_output_is_not_treated_as_debris_by_resynthesis():
    # 좁아진 목표 구간이 다음 단계의 불가능 뭉침 게이트에 걸리면 균등 분배가 융합을 덮어
    # 수정이 무효화된다 — 걸리지 않아야 한다(발음 구간은 라인 폭의 88%).
    text = "あいうえおかきくけこ"
    ko = [_clumped(text, LINE_START, LINE_END)]
    ja = [_spread(text, 100.0, 103.14)]
    pron_data = {0: {"pron_segments": _pron(len(text), LINE_START, VOCAL_END)}}
    fixes: dict[int, list[str]] = {}

    assert _fuse_original_char_timing(
        ko, ja, fixes, max_char_rate=11.0, pron_data=pron_data
    ) == {0}
    assert _synthesize_collapsed_timing(
        ko, pron_data, fixes, song_conf=None, threshold=0.0, max_char_rate=11.0
    ) == set()
    assert abs(ko[0].word_segments[-1].end - VOCAL_END) < 1e-9  # 융합 분포가 살아 있다


def test_fuse_clamps_pron_window_into_the_line_bounds():
    # 발음 음절이 라인 시작보다 앞에서 시작하면(경계 보정으로 라인 시작이 뒤로 밀린 경우)
    # 그 구간을 라인 경계로 잘라 쓴다 — 글자가 라인 밖으로 나가면 안 된다.
    text = "あいうえおかきくけこ"
    ko = [_clumped(text, LINE_START, LINE_END)]
    ja = [_spread(text, 100.0, 103.14)]
    pron = _pron(len(text), LINE_START - 0.3, VOCAL_END)
    pron_data = {0: {"pron_segments": pron}}

    assert _fuse_original_char_timing(
        ko, ja, {}, max_char_rate=11.0, pron_data=pron_data
    ) == {0}
    ws = ko[0].word_segments
    assert abs(ws[0].start - LINE_START) < 1e-9  # 9.7이 아니라 라인 시작
    assert abs(ws[-1].end - VOCAL_END) < 1e-9


# ---- 폴백: 발음 음절로 구간을 못 정하면 예전처럼 라인 경계로 사상 -----------------


def test_fuse_falls_back_to_line_bounds_without_pron_data():
    # 발음이 없는 곡(한국어·영어)은 융합 경로 자체가 pron_data 없이 돈다 — 기존 동작 유지
    text = "あいうえおかきくけこ"
    ko = [_clumped(text, LINE_START, LINE_END)]
    ja = [_spread(text, 100.0, 103.14)]
    assert _fuse_original_char_timing(ko, ja, {}, max_char_rate=11.0) == {0}
    ws = ko[0].word_segments
    assert abs(ws[0].start - LINE_START) < 1e-9 and abs(ws[-1].end - LINE_END) < 1e-9


def test_fuse_falls_back_on_lines_whose_pron_segments_are_missing_or_void():
    # 커버리지 0.9 게이트를 통과한 곡에도 발음 표기가 빠진 라인이 있고, 누출 스플라이스는
    # 그 라인의 pron_segments를 None으로 무효화한다 — 그 라인만 라인 경계로 사상한다.
    text = "あいうえおかきくけこ"
    for pron_data in (
        {},                                   # 그 라인 항목 자체가 없음
        {0: {}},                              # 항목은 있으나 키가 없음
        {0: {"pron_segments": None}},         # 스플라이스가 무효화
        {0: {"pron_segments": []}},           # 빈 목록
        {0: {"pron_segments": [{"text": "가", "start": None, "end": None}]}},  # 시간 없음
    ):
        ko = [_clumped(text, LINE_START, LINE_END)]
        ja = [_spread(text, 100.0, 103.14)]
        assert _fuse_original_char_timing(
            ko, ja, {}, max_char_rate=11.0, pron_data=pron_data
        ) == {0}
        ws = ko[0].word_segments
        assert abs(ws[0].start - LINE_START) < 1e-9
        assert abs(ws[-1].end - LINE_END) < 1e-9


def test_fuse_falls_back_when_pron_segments_are_stale_after_a_line_moved():
    # pron_segments는 results와 별개 자료구조라 스냅·클램프로 라인이 옮겨져도 함께 안 움직인다.
    # 원래 자리에 남은 stale 값을 목표로 쓰면 글자가 라인 밖으로 날아간다 — 라인 경계 폴백.
    text = "あいうえおかきくけこ"
    ko = [_clumped(text, 40.0, 43.54)]
    ja = [_spread(text, 100.0, 103.14)]
    pron_data = {0: {"pron_segments": _pron(len(text), LINE_START, VOCAL_END)}}

    assert _fuse_original_char_timing(
        ko, ja, {}, max_char_rate=11.0, pron_data=pron_data
    ) == {0}
    ws = ko[0].word_segments
    assert abs(ws[0].start - 40.0) < 1e-9 and abs(ws[-1].end - 43.54) < 1e-9


def test_fuse_falls_back_when_pron_window_is_too_narrow_for_the_chars():
    # 음절이 한두 개만 살아남아 구간이 지나치게 좁으면(10글자를 0.2s에) 그 창에 욱여넣은
    # 결과가 곧바로 불가능 뭉침 게이트에 걸린다 — 좁은 창 대신 라인 경계로 사상한다.
    text = "あいうえおかきくけこ"
    ko = [_clumped(text, LINE_START, LINE_END)]
    ja = [_spread(text, 100.0, 103.14)]
    pron_data = {0: {"pron_segments": _pron(1, LINE_START, LINE_START + 0.2)}}

    assert _fuse_original_char_timing(
        ko, ja, {}, max_char_rate=11.0, pron_data=pron_data
    ) == {0}
    ws = ko[0].word_segments
    assert abs(ws[-1].end - LINE_END) < 1e-9


def test_fuse_stays_monotonic_inside_the_pron_window_on_nonmonotonic_ja():
    ko = [_clumped("あいうえお", LINE_START, LINE_END)]
    jumbled = SyncResult(
        text="あいうえお",
        start_time=0.0,
        end_time=10.0,
        word_segments=[
            _ws("あ", 0.0, 2.0, 0.02),
            _ws("い", 6.0, 8.0, 0.02),
            _ws("う", 3.0, 4.0, 0.02),  # 역행
            _ws("え", 8.0, 9.0, 0.02),
            _ws("お", 9.0, 10.0, 0.02),
        ],
    )
    pron_data = {0: {"pron_segments": _pron(5, LINE_START, VOCAL_END)}}
    assert _fuse_original_char_timing(
        ko, [jumbled], {}, max_char_rate=0.0, pron_data=pron_data
    ) == {0}
    ws = ko[0].word_segments
    assert _starts(ws) == sorted(_starts(ws))
    assert all(ws[i].end <= ws[i + 1].start + 1e-9 for i in range(len(ws) - 1))
    assert ws[0].start >= LINE_START - 1e-9 and ws[-1].end <= VOCAL_END + 1e-9


# ---- _measured_vocal_window: 목표 구간 결정 규칙 ---------------------------------


def test_measured_vocal_window_rules():
    pron = _pron(10, LINE_START, VOCAL_END)
    # 정상: 첫 음절 start ~ 마지막 음절 end
    win = _measured_vocal_window(pron, LINE_START, LINE_END, 10, 11.0)
    assert win is not None and abs(win[0] - LINE_START) < 1e-9 and abs(win[1] - VOCAL_END) < 1e-9
    # 발음 없음
    assert _measured_vocal_window(None, LINE_START, LINE_END, 10, 11.0) is None
    assert _measured_vocal_window([], LINE_START, LINE_END, 10, 11.0) is None
    # 라인 경계 밖 1초 초과(stale) — 앞·뒤 양방향
    assert _measured_vocal_window(pron, LINE_START + 1.5, LINE_END, 10, 11.0) is None
    assert _measured_vocal_window(pron, LINE_START, VOCAL_END - 1.5, 10, 11.0) is None
    # 1초 이내로 벗어난 값은 경계로 잘라 쓴다
    win = _measured_vocal_window(pron, LINE_START + 0.5, LINE_END, 10, 11.0)
    assert win is not None and abs(win[0] - (LINE_START + 0.5)) < 1e-9
    # 폭 0 (한 음절의 start==end)
    zero = [{"text": "가", "start": LINE_START, "end": LINE_START}]
    assert _measured_vocal_window(zero, LINE_START, LINE_END, 10, 11.0) is None
    # 좁은 창 + 글자 과다 → max_char_rate 초과
    narrow = _pron(1, LINE_START, LINE_START + 0.2)
    assert _measured_vocal_window(narrow, LINE_START, LINE_END, 10, 11.0) is None
    # 같은 창이라도 글자가 적으면 통과하고, 게이트를 끄면(0) 언제나 통과
    assert _measured_vocal_window(narrow, LINE_START, LINE_END, 2, 11.0) is not None
    assert _measured_vocal_window(narrow, LINE_START, LINE_END, 10, 0.0) is not None


# ---- _measured_anchor_count: 직렬화와 동일 규칙 ---------------------------------


def test_measured_anchor_count_counts_distinct_times_not_chars():
    text = "繰り返しあ"
    # 4글자 토큰 + 1글자 토큰 = 글자 5개지만 서로 다른 시각은 2개
    assert _measured_anchor_count(text, [_ws("繰り返し", 0.0, 1.0), _ws("あ", 1.0, 2.0)]) == 2
    # 같은 시각을 공유하는 글자들(역매핑 복사)은 앵커 1개
    assert _measured_anchor_count(text, [_ws(c, 3.0, 4.0) for c in text]) == 1
    assert _measured_anchor_count(text, None) == 0
    assert _measured_anchor_count(text, [_ws("", 0.0, 1.0)]) == 0
    # 본문에 없는 표기 차이 토큰은 스퓨리어스로 버려져 앵커로 안 센다
    assert _measured_anchor_count(text, [_ws("ザザザ", 0.0, 1.0)]) == 0
