"""합성보컬 대량 역누출 재배치 스냅(_snap_post_interlude_leak) 회귀 테스트.

熱異常(b2NTglk9tvI) 실측: 足立レイ 합성보컬이라 CTC posterior가 균일 바닥(라인 92.5%가
conf<0.001)이어서 음향 앵커가 전멸, 32.7s 간주(132.8→165.5) 뒤 리프라이즈 블록이 간주를
통째로 건너뛰어 붕괴 — 선두 idx51-52가 129.9/130.7s로 크램, 이후 26줄 중앙값 -14.75s(최대
-39.8s). ja 대조는 ja도 같이 붕괴해 무력하므로 간주(무음)에 앵커해 재배치한다.
정상적으로 간주를 대기한 커버(hDhjRh-Gt4g: 직전 라인 130.2s→다음 165.6s)는 무변경이어야 한다.
"""
from everyric2.audio.vad import VocalRegion
from everyric2.inference.prompt import SyncResult
from everyric2.server.worker import _snap_post_interlude_leak


class _Vad:
    def __init__(self, regions):
        self.regions = regions


def _regions(*spans: tuple[float, float]) -> "_Vad":
    return _Vad([VocalRegion(start=s, end=e, energy=0.1) for s, e in spans])


def _line(start: float, end: float, text: str = "x") -> SyncResult:
    return SyncResult(text=text, start_time=start, end_time=end)


def _netsu_lines():
    """熱異常 실측 축약: 검증부(0-1), 정상 마지막 라인(2), 크램 누출(3-4), 압축 후반(5-9)."""
    return [
        _line(120.0, 121.0, "黒い星が"),           # 0 초기 verse
        _line(125.8, 126.5, "彼らを見ている"),       # 1 (직전-직전, 정상 간격)
        _line(129.5, 130.0, "私を見ている"),         # 2 cue50: 정상 마지막(간격 3.7s) — 보존돼야
        _line(129.9, 130.7, "死んだ変数で繰り返す"),  # 3 cue51 누출: 10자/0.8s=12.5자/s (불가능)
        _line(130.7, 131.5, "数え事が孕んだ熱"),      # 4 cue52 누출: 간격 0.8s
        _line(165.5, 166.5, "どこに送るあてもなく"),  # 5 cue53 간주 이후(압축)
        _line(170.9, 172.0, "泣いた細胞が海に戻る"),  # 6
        _line(182.7, 183.5, "希望で手が汚れてる"),    # 7
        _line(214.9, 216.0, "叫んだ音は既に列を成さないで"),  # 8
        _line(225.1, 226.0, "なにかが来ている"),      # 9 마지막
    ]
    # 간주 무음 [132.8, 165.5] = 32.7s


NETSU_VAD = _regions((0.0, 132.8), (165.5, 227.5))


def test_mass_leak_respaces_collapsed_reprise_across_post_interlude_vocal():
    res = _netsu_lines()
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, NETSU_VAD, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    # 누출 클러스터(3,4)부터 세그먼트 끝(9)까지 재배치, 정상 마지막(2) 이전은 보존
    assert clamped == {3, 4, 5, 6, 7, 8, 9}
    assert 2 not in clamped and res[2].start_time == 129.5  # cue50 그대로
    assert res[0].start_time == 120.0 and res[1].start_time == 125.8
    # 완전 누출된 선두 두 줄이 gap_end(165.5) 이후로 앵커됨 (기존 129.9/130.7 → 165.5+)
    assert res[3].start_time >= 165.5
    assert res[4].start_time > res[3].start_time
    # 순서 단조 유지
    starts = [res[i].start_time for i in range(3, 10)]
    assert starts == sorted(starts)
    # 마지막 라인은 발성 구간 끝(227.5) 근처
    assert res[9].start_time <= 227.5


def test_mass_leak_leaves_benign_cover_untouched():
    # 커버: 간주를 정상 대기 — cue50 130.2s 직후 바로 다음 라인이 165.6s(간주 이후).
    # gs(132.8) 직전 라인이 1개(정상 간격)라 크램 클러스터가 비어 무변경.
    res = [
        _line(120.0, 121.0, "黒い星が"),
        _line(126.1, 126.8, "彼らを見ている"),
        _line(130.2, 130.9, "私を見ている"),        # 직전 라인(정상 간격 4.1s)
        _line(165.6, 166.6, "死んだ変数で繰り返す"),  # 간주 이후 정착
        _line(171.0, 172.0, "数え事が孕んだ熱"),
    ]
    snapshot = [(r.start_time, r.end_time) for r in res]
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, NETSU_VAD, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    assert clamped == set()
    assert [(r.start_time, r.end_time) for r in res] == snapshot


def test_mass_leak_char_rate_gate_protects_real_fast_section():
    # 간주 앞에 촘촘한 클러스터가 있어도, 짧은 가사(사람이 낼 수 있는 rate)면 실제 빠른
    # 구간으로 보고 건드리지 않는다 (정상 곡 오탐 방지).
    res = [
        _line(120.0, 121.0, "黒い星が"),
        _line(125.8, 126.5, "彼らを見ている"),
        _line(129.5, 130.0, "あ"),   # 1자
        _line(129.9, 130.7, "い"),   # 간격 0.4s이지만 1자/0.8s=1.25자/s (정상)
        _line(130.7, 131.5, "う"),
        _line(165.5, 166.5, "どこに送るあてもなく"),
        _line(170.9, 172.0, "泣いた細胞が海に戻る"),
    ]
    snapshot = [(r.start_time, r.end_time) for r in res]
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, NETSU_VAD, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    assert clamped == set()
    assert [(r.start_time, r.end_time) for r in res] == snapshot


def test_mass_leak_ignores_short_interlude():
    # 12s 미만 간주는 리프라이즈를 숨길 수 없다 → 발동 안 함.
    short_vad = _regions((0.0, 130.0), (138.0, 200.0))  # 간주 8s
    res = _netsu_lines()
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, short_vad, clamped, min_gap_sec=12.0, min_char_rate=11.0)
    assert clamped == set()


def test_mass_leak_disabled_when_min_gap_zero():
    res = _netsu_lines()
    clamped: set[int] = set()
    _snap_post_interlude_leak(res, NETSU_VAD, clamped, min_gap_sec=0.0, min_char_rate=11.0)
    assert clamped == set()
