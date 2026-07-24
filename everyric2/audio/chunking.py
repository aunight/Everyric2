"""길이 무관 피크 VRAM을 위한 오버랩 청크 계획 + 시간축 스티칭 유틸.

긴 오디오를 통짜로 신경망에 넣으면 forward-pass 활성값이 오디오 길이에 비례해
무한 증가한다 — 실사고(2026-07-24): 공유 RTX 3090(24GB)에서 17분 곡의 CTC 정렬/
멜로디 f0가 CUDA OOM(단일 6.29GiB 할당 실패). 3~5분 곡은 통과하고 길이만 커지면
터지는 전형적인 '단일 잡 내부 활성 피크가 길이 비례' 패턴이다.

대책: 오디오를 겹침(overlap) 있는 샘플 윈도로 나눠 청크별로 추론하고, 각 청크 출력의
'신뢰 중앙 구간'만 골라 시간축으로 이어붙여 통짜 출력을 근사 복원한다. 청크 경계의
수용영역(receptive field) 오염은 겹침을 절반씩 버려 제거한다(표준 overlap-crop 기법).
피크 VRAM은 청크 길이에만 의존하므로 오디오가 아무리 길어도 상한이 생긴다.

짧은 곡(윈도 1개)은 크롭이 전무 → 통짜 경로와 완전히 동일한 출력을 낸다. 그래서
청크 크기 기본값을 기존 통과 사례(5분)보다 크게 두면 짧은 곡의 정렬 품질은 불변이다.

프레임↔샘플 매핑은 청크마다 실제 출력 길이로 비율을 잡으므로(전역 고정 fps 가정 없음)
conv 경계의 ±1 프레임 슬랙에도 견고하다. 순수 함수라 GPU/모델 없이 합성 데이터로
동등성을 검증할 수 있다(tests/test_chunking.py).
"""

from __future__ import annotations

import numpy as np


def plan_chunk_windows(
    n_samples: int, chunk_samples: int, overlap_samples: int
) -> list[tuple[int, int]]:
    """[0, n_samples)를 덮는 겹침 샘플 윈도 목록 [(start, end), ...]을 만든다.

    각 윈도 길이는 chunk_samples 이하, 인접 윈도는 overlap_samples만큼 겹친다. 마지막
    윈도의 end는 n_samples로 클램프한다. chunk_samples가 0 이하이거나 오디오가 한 청크에
    들어가면 [(0, n_samples)] 하나만 돌려준다(청킹 비활성 = 통짜 경로).
    """
    if n_samples <= 0:
        return [(0, max(0, n_samples))]
    if chunk_samples <= 0 or n_samples <= chunk_samples:
        return [(0, n_samples)]
    overlap_samples = max(0, min(overlap_samples, chunk_samples - 1))
    stride = chunk_samples - overlap_samples
    windows: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + chunk_samples, n_samples)
        windows.append((start, end))
        if end >= n_samples:
            break
        start += stride
    return windows


def keep_ranges_for_windows(
    windows: list[tuple[int, int]], n_samples: int
) -> list[tuple[int, int]]:
    """윈도별로 '채택할 샘플 구간' [ks, ke)을 낸다 — [0, n_samples)를 빈틈·겹침 없이 타일링.

    인접 윈도 경계는 겹침 구간의 중앙(midpoint)으로 자른다: 앞 윈도는 중앙까지, 뒤 윈도는
    중앙부터 채택한다. 첫 윈도는 0에서, 마지막 윈도는 n_samples까지.
    """
    m = len(windows)
    ranges: list[tuple[int, int]] = []
    for i, (s, e) in enumerate(windows):
        ks = 0 if i == 0 else (windows[i][0] + windows[i - 1][1]) // 2
        ke = n_samples if i == m - 1 else (windows[i + 1][0] + windows[i][1]) // 2
        ranges.append((ks, ke))
    return ranges


def _slice_frames(arr, frame_axis: int, f_start: int, f_end: int):
    idx: list = [slice(None)] * arr.ndim
    idx[frame_axis] = slice(f_start, f_end)
    return arr[tuple(idx)]


def stitch_chunk_outputs(
    outputs: list, windows: list[tuple[int, int]], n_samples: int, frame_axis: int = 0
):
    """청크 출력들을 신뢰 중앙 구간만 골라 시간축(frame_axis)으로 이어붙인다.

    outputs[i]는 windows[i](샘플 [s, e))에 대응하는 프레임 시퀀스(numpy 배열 또는 torch
    텐서, frame_axis가 프레임 축). 각 청크의 채택 샘플 구간을 그 청크의 실제 출력 길이로
    비율 매핑해 프레임 인덱스로 바꾼 뒤 잘라 concat한다. 윈도가 1개면 크롭이 전 구간이라
    입력을 그대로 돌려준다(통짜와 동일). numpy면 np.concatenate, torch면 torch.cat.
    """
    if not outputs:
        raise ValueError("stitch_chunk_outputs: empty outputs")
    keeps = keep_ranges_for_windows(windows, n_samples)
    pieces = []
    for out, (s, e), (ks, ke) in zip(outputs, windows, keeps):
        t = int(out.shape[frame_axis])
        span = max(1, e - s)
        f_start = round((ks - s) * t / span)
        f_end = round((ke - s) * t / span)
        f_start = max(0, min(t, f_start))
        f_end = max(f_start, min(t, f_end))
        pieces.append(_slice_frames(out, frame_axis, f_start, f_end))
    if len(pieces) == 1:
        return pieces[0]
    if isinstance(pieces[0], np.ndarray):
        return np.concatenate(pieces, axis=frame_axis)
    import torch

    return torch.cat(pieces, dim=frame_axis)
