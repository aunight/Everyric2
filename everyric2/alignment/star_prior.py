"""star 채널의 프레임별 성형 값 — «금지»가 아니라 «가격 차이»로 배치를 이끈다.

## 왜 (조사 종합 2026-07-26 · 01절)

torchaudio 계열의 와일드카드 ``<star>``는 ``log p = 0``(= p = 1)으로 붙는다. 모든
프레임에서 최우선이고 **어디서 흡수하든 비용이 같아서**, posterior가 균일 바닥인
곡(합성보컬)에서는 「글자를 앞으로 몰고 뒤를 star로 비우기」가 최적해가 된다 —
간주 오배치·재실행 비결정성·하드 금지 마스크의 실패가 전부 이 동점에서 나온다
(``ctc_engine.py``의 star 주석과 ``caption_anchors.py`` 모듈 주석에 실측 근거).

여기서는 star 열을 상수 0 대신 프레임별 값으로 성형한다:

    노래가 있는 프레임 (f0 유성)   → -weight × presence   star가 비싸다 → 글자가 선호된다
    간주·무음 (f0 무성)            → 0                     star가 싸다   → star가 흡수한다

하드 마스킹(-1e4)은 남은 자리를 전부 동점으로 두지만, 가격 차이는 DP가 스스로
「여기가 싸다」를 따라가게 만든다. 글자 토큰이 유성 프레임 하나를 차지할 때마다 star가
그 프레임을 흡수할 비용(weight)이 절약되므로, 유성 구간에 글자를 두는 배치가 프레임당
weight nats씩 이긴다 — 균일 바닥에서도 동점이 사라진다.

설계 제약 (US9305530B1, Amazon, 유효): 「ML로 노래/비노래 구간을 판별해 가사를 **줄
단위로 그 구간에 통째 배치**」가 청구 범위다. 이 모듈은 구간 판별도 줄 배치도 하지
않는다 — 글자 단위 CTC 정렬의 와일드카드 채널에 프레임별 사전확률을 로그 공간에서
더하는 것이고, 글자를 어디에 둘지는 여전히 DP가 음향 근거로 정한다.

## 신호 — 우세도가 기본이고 f0는 폴백이다 (실측 2026-07-27, zyRt-nBM3dY)

**f0 유성 지시자는 분리 스템 위에서 죽는다.** Demucs가 간주에도 잔류 신호를 남기고
FCPE(임계 0.006)가 그것을 전부 유성으로 잡는다 — 사고 곡의 크레디트 간주(8.7~24.9s)의
f0 presence가 0.979로 **전 구간 최고**였다. 스템 자체의 RMS도 못 가른다(간주 p50
-23.4dB vs 가창 -19.8dB, 3dB 차이). VAD가 같은 곡에서 실패한 것과 같은 이유다
(``caption_anchors.py`` 모듈 주석 — 상대 백분위 게이트, 절대 플로어 없음).

가르는 것은 **보컬 우세도** — 보컬 스템과 반주 스템의 프레임 RMS 비율이다:

    dominance = rms(vocals) / (rms(vocals) + rms(accompaniment))
    실측: 크레디트 간주 0.199 · 가창 구간 0.36~0.68 (3배 대비)

반주가 큰 곳에서 낮아지므로 절대 음량과 무관하고, 자기 분포가 아니라 **반주를 기준으로**
정규화하므로 VAD의 백분위 함정도 피한다. 라인 사이의 짧은 쉼에서도 우세도가 내려앉아
star가 정확히 그 자리에서 싸진다 — 줄 간 슬랙의 가격이 자동으로 매겨진다.

f0 유성 경로(``vocal_presence_from_f0``)는 반주 스템이 없을 때(분리 미설치·믹스 정렬)의
폴백으로 남긴다. 이동평균 smoothing은 두 경로 공통이다 — 무성 자음(s/t/k류)과 호흡은
노래 도중에도 신호를 수십 ms씩 떨어뜨리므로, 그대로 쓰면 가사 한복판에서 star가
공짜가 된다.

이 모듈은 순수 numpy다 — torch도 IO도 없다. star 열 구성은 ``ctc_engine``이 한다.
"""

from __future__ import annotations

import numpy as np

# 우세도 → presence 소프트 게이트의 경계. 실측(zyRt-nBM3dY)에서 간주 p50 0.15,
# 가창 구간 p50 0.36~0.69였다 — 0.2 아래는 확실한 비가창(0), 0.4 위는 확실한
# 가창(1), 사이는 선형. 붕괴 곡 하나의 실측이므로 3곡 A/B에서 재검증한다.
_DOM_LO = 0.2
_DOM_HI = 0.4


def vocal_presence_from_f0(
    f0_hz, times, smooth_sec: float = 0.4
) -> tuple[np.ndarray, np.ndarray] | None:
    """f0 곡선 → (times, presence ∈ [0,1]). 신호가 못 쓸 모양이면 None (성형 생략).

    presence는 유성 지시자(f0 > 0)의 ``smooth_sec`` 이동평균이다 — 창 안에서 유성인
    프레임의 비율이므로 자연히 [0,1]이고, 간주 경계에서는 창 절반(~0.2s) 안에서
    선형으로 오르내린다. Durand 2023의 소프트 마스크 경계 완충과 같은 역할이다.
    """
    f0 = np.asarray(f0_hz, dtype=np.float64).reshape(-1)
    t = np.asarray(times, dtype=np.float64).reshape(-1)
    if f0.size < 2 or t.size != f0.size:
        return None
    voiced = (f0 > 0).astype(np.float64)
    if smooth_sec > 0:
        dt = float(np.median(np.diff(t)))
        if dt <= 0:
            return None
        win = int(round(smooth_sec / dt))
        if win > 1:
            voiced = np.convolve(voiced, np.ones(win) / win, mode="same")
    return t, np.clip(voiced, 0.0, 1.0)


def _mono(waveform: np.ndarray) -> np.ndarray:
    w = np.asarray(waveform, dtype=np.float64)
    return w.mean(axis=0) if w.ndim == 2 else w


def frame_rms(waveform: np.ndarray, sample_rate: int, hop_sec: float = 0.01) -> np.ndarray:
    """10ms 프레임 RMS. 시각 격자는 (i + 0.5) × hop_sec — f0 격자와 같은 규약."""
    w = _mono(waveform)
    hop = max(1, int(round(sample_rate * hop_sec)))
    n = len(w) // hop
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    return np.sqrt(np.mean(w[: n * hop].reshape(n, hop) ** 2, axis=1))


def vocal_presence_from_stems(
    vocals_waveform: np.ndarray,
    accomp_waveform: np.ndarray,
    sample_rate: int,
    smooth_sec: float = 0.4,
    hop_sec: float = 0.01,
) -> tuple[np.ndarray, np.ndarray] | None:
    """보컬/반주 스템 → (times, presence ∈ [0,1]). 우세도 기반 — 모듈 주석의 근거 참조."""
    v = frame_rms(vocals_waveform, sample_rate, hop_sec)
    a = frame_rms(accomp_waveform, sample_rate, hop_sec)
    n = min(len(v), len(a))
    if n < 2:
        return None
    dom = v[:n] / (v[:n] + a[:n] + 1e-10)
    presence = np.clip((dom - _DOM_LO) / (_DOM_HI - _DOM_LO), 0.0, 1.0)
    if smooth_sec > 0:
        win = int(round(smooth_sec / hop_sec))
        if win > 1:
            presence = np.convolve(presence, np.ones(win) / win, mode="same")
    times = (np.arange(n, dtype=np.float64) + 0.5) * hop_sec
    return times, np.clip(presence, 0.0, 1.0)


def star_frame_scores(
    presence_times: np.ndarray,
    presence: np.ndarray,
    num_frames: int,
    sec_per_frame: float,
    weight: float,
) -> np.ndarray:
    """presence(10ms 격자)를 CTC 프레임 격자(20ms)로 옮긴 star 로그확률 성형값 (≤ 0).

    프레임 중심 시각으로 선형 보간한다. presence 범위 밖(오디오 길이 불일치의 꼬리)은
    0 — 신호가 없는 곳에서 star를 비싸게 만들면 안 된다(보수적 실패).
    """
    if num_frames <= 0:
        return np.zeros(0, dtype=np.float64)
    if weight <= 0:
        return np.zeros(num_frames, dtype=np.float64)
    centers = (np.arange(num_frames, dtype=np.float64) + 0.5) * float(sec_per_frame)
    p = np.interp(centers, presence_times, presence, left=0.0, right=0.0)
    return -float(weight) * p
