"""대량 역누출 재배치 스냅(_snap_post_interlude_leak) + 무음 좌초 스냅(_snap_silence_undershoot)
회귀 테스트.

핵심 게이트는 **VAD 발성 커버리지**다: 라인 스팬이 발성 위에 있으면(정상 배치) 절대 이동하지
않고, 무음 위에 떠 있는(<max_coverage) 라인만 누출로 인정한다. char-rate는 보조 게이트.

실전 회귀 배경:
- 消失(초고속): 정상 가창 라인도 간격<1.5s·고밀도라, 간격/char-rate만으로는 발성 위 정상
  라인을 크램으로 오판해 +24~38s 밀어 회귀시켰다(mean|res| 2.50→12.15). 커버리지 게이트가
  발성 위 라인을 보호한다.
- 熱異常(합성보컬): 진짜 리프라이즈 크램은 간주 무음 위에 떠 있어(커버리지 0) 발동해야 한다.
"""
from everyric2.audio.vad import VocalRegion
from everyric2.inference.prompt import SyncResult
from everyric2.server.worker import _snap_post_interlude_leak, _snap_silence_undershoot


class _Vad:
    def __init__(self, regions):
        self.regions = regions


def _regions(*spans: tuple[float, float]) -> "_Vad":
    return _Vad([VocalRegion(start=s, end=e, energy=0.1) for s, e in spans])


def _line(start: float, end: float, text: str = "x") -> SyncResult:
    return SyncResult(text=text, start_time=start, end_time=end)


# ---- 熱異常: 무음 위 리프라이즈 크램 → 발동 -------------------------------------
# VAD: 발성 [0,128]·[165.5,227.5], 간주 무음 [128,165.5]=37.5s.
# 실제 마지막 verse(idx2)는 발성 위(보호), 누출 리프라이즈(idx3-4)는 무음 위(발동).
NETSU_VAD = _regions((0.0, 128.0), (165.5, 227.5))


def _netsu_lines():
    return [
        _line(120.0, 121.0, "黒い星が"),            # 0 verse (발성 위)
        _line(124.0, 125.0, "彼らを見ている"),        # 1 verse (발성 위)
        _line(126.0, 127.5, "私を見ている"),          # 2 cue50 정상 마지막 (발성 위 → 보호)
        _line(129.9, 130.7, "死んだ変数で繰り返す"),   # 3 cue51 누출: 무음 위, 11자/0.8s=13.8/s
        _line(130.7, 131.5, "数え事が孕んだ熱"),       # 4 cue52 누출: 무음 위
        _line(165.5, 166.5, "どこに送るあてもなく"),   # 5 cue53 간주 이후(압축)
        _line(170.9, 172.0, "泣いた細胞が海に戻る"),   # 6
        _line(182.7, 183.5, "希望で手が汚れてる"),     # 7
        _line(214.9, 216.0, "叫んだ音は既に列を成さないで"),  # 8
        _line(225.1, 226.0, "なにかが来ている"),       # 9
    ]


def test_mass_leak_fires_on_silence_floating_cram():
    res = _netsu_lines()
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, NETSU_VAD, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    # 무음 위 누출(3,4)부터 세그먼트 끝(9)까지 재배치. 발성 위 정상 라인(0-2)은 보호.
    assert clamped == {3, 4, 5, 6, 7, 8, 9}
    assert 0 not in clamped and 1 not in clamped and 2 not in clamped
    assert res[2].start_time == 126.0  # cue50 그대로
    # 완전 누출된 선두 줄이 gap_end(165.5) 이후로 앵커
    assert res[3].start_time >= 165.5
    starts = [res[i].start_time for i in range(3, 10)]
    assert starts == sorted(starts)          # 단조
    assert res[9].start_time <= 227.5         # 발성 끝 이내


def test_mass_leak_protects_lines_sitting_on_vocal_shoushitsu_regression():
    # 消失 회귀 케이스: 간주 [43.76,59.84] 앞 idx0~6이 35~43s **발성 위**에 정상 배치.
    # 초고속이라 간격<1.5s·고밀도지만 발성 위이므로 절대 이동 금지 → 스냅 0줄.
    vad = _regions((27.0, 43.76), (59.84, 90.0))
    res = [
        _line(35.0, 35.9, "暴走の果てに見える"),
        _line(36.0, 36.9, "終わる世界"),
        _line(37.0, 37.9, "ボクは生まれ"),
        _line(38.0, 38.9, "そして気づく"),
        _line(39.0, 39.9, "所詮ヒトの"),
        _line(40.0, 40.9, "真似事だと"),
        _line(42.0, 43.0, "永遠の命VOCALOID"),   # 마지막 인트로 라인 (발성 위, 고밀도)
        _line(59.84, 62.0, "ボクガ上手ク歌エナイトキモ"),  # 간주 이후 나레이션
        _line(68.0, 70.0, "一緒ニ居テクレタ"),
        _line(75.0, 77.0, "かつて歌うこと"),
    ]
    snapshot = [(r.start_time, r.end_time) for r in res]
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, vad, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    assert clamped == set()
    assert [(r.start_time, r.end_time) for r in res] == snapshot


def test_mass_leak_char_rate_gate_protects_slow_silence_line():
    # 무음 위에 떠 있어도(커버리지 통과) 짧은 가사(사람 속도)면 char-rate 보조 게이트가 막는다.
    res = [
        _line(120.0, 121.0, "黒い星が"),
        _line(126.0, 127.5, "私を見ている"),   # 발성 위 경계
        _line(129.9, 130.7, "あ"),            # 무음 위지만 1자/0.8s=1.25자/s
        _line(130.7, 131.5, "い"),
        _line(165.5, 166.5, "どこに送るあてもなく"),
        _line(170.9, 172.0, "泣いた細胞が海に戻る"),
    ]
    snapshot = [(r.start_time, r.end_time) for r in res]
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, NETSU_VAD, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    assert clamped == set()
    assert [(r.start_time, r.end_time) for r in res] == snapshot


def test_mass_leak_movement_capped_by_gap_length():
    # 이동량 상한: 어떤 라인도 (간주 길이 + 여유) 이상 앞으로 재배치되지 않는다.
    res = _netsu_lines()
    clamped: set[int] = set()
    orig = [r.start_time for r in _netsu_lines()]
    _snap_post_interlude_leak(res, NETSU_VAD, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    gap = 165.5 - 128.0
    for i in clamped:
        assert res[i].start_time - orig[i] <= gap + 5.0 + 1e-9


def test_mass_leak_ignores_short_interlude():
    short_vad = _regions((0.0, 130.0), (138.0, 200.0))  # 간주 8s < 12
    res = _netsu_lines()
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, short_vad, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    assert clamped == set()


def test_mass_leak_disabled_when_min_gap_zero():
    res = _netsu_lines()
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, NETSU_VAD, clamped, min_gap_sec=0.0, min_char_rate=11.0)
    assert clamped == set()


# ---- bug #5: 무음 좌초 '쌍/블록'의 첫 줄도 스냅 -------------------------------


def test_silence_undershoot_snaps_first_of_stranded_pair():
    # 간주 무음에 좌초한 리프라이즈 두 줄. 예전엔 첫 줄이 (아직 안 옮겨진) 둘째 줄에 막혀
    # 스킵되고 2번째 줄만 회복됐다 — 이제 첫 줄부터 gap_end 이후로 스냅돼야 한다.
    vad = _regions((0.0, 128.0), (165.5, 200.0))
    res = [
        _line(126.0, 127.5, "私を見ている"),        # 0 발성 위 (정상)
        _line(130.0, 130.8, "死んだ変数で繰り返す"),  # 1 무음 좌초 (리프라이즈 1번째)
        _line(131.0, 131.8, "数え事が孕んだ熱"),      # 2 무음 좌초 (리프라이즈 2번째)
        _line(165.5, 166.5, "どこに送るあてもなく"),  # 3 발성 위 (정착)
    ]
    clamped: set[int] = set()
    _snap_silence_undershoot(res, vad, clamped)
    assert 1 in clamped and 2 in clamped          # 첫 줄·둘째 줄 둘 다 스냅
    assert res[1].start_time >= 165.0              # 첫 줄이 gap_end 이후로
    assert res[1].start_time < res[2].start_time  # 순서 유지(겹침 없음)
    assert res[2].start_time <= res[3].start_time
    assert 0 not in clamped and res[0].start_time == 126.0  # 발성 위 라인 보호
