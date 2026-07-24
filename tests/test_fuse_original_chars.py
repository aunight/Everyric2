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
"""
from everyric2.config.settings import AlignmentSettings
from everyric2.inference.prompt import SyncResult, WordSegment
from everyric2.server.worker import (
    _dual_align_should_run,
    _full_coverage_words,
    _fuse_original_char_timing,
    _measured_anchor_count,
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
