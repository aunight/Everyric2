"""오버랩 청크 분할·스티칭 동등성 — GPU/실모델 없이 합성 데이터로 검증.

계약(실사고 2026-07-24 CUDA OOM 재발 방지): 긴 오디오를 겹침 청크로 나눠 청크별 추론
후 시간축으로 스티칭한 결과가 통짜 추론과 동등해야 한다. 프레임이 국소(수용영역 << 겹침)
이고 청크 경계가 프레임 격자에 정렬되면 스티칭 결과는 통짜와 완전히 동일하다 — 즉 짧은
곡(단일 청크)뿐 아니라 청크 경로 자체가 통짜를 정확히 근사한다.

'국소 프레임 모델'을 합성해(프레임 j = 그 프레임 블록의 로컬 평균, 청크 경계 무관) 청크
분할·중앙 채택·스티칭의 인덱스 산술만 순수하게 태운다. CTC(_ctc_log_emission)·멜로디
(_infer_f0)의 실제 청크 경로도 모델 forward만 합성으로 몽키패치해 통짜와 대조한다.
"""

import numpy as np

from everyric2.audio.chunking import (
    keep_ranges_for_windows,
    plan_chunk_windows,
    stitch_chunk_outputs,
)

CTC_STRIDE = 320  # wav2vec2 samples/frame
F0_HOP = 160  # RMVPE/FCPE samples/frame (10ms @ 16k)


def _local_frames(wave: np.ndarray, stride: int, n_ch: int = 1) -> np.ndarray:
    """no-context 국소 프레임 모델: 프레임 j = wave[j*stride:(j+1)*stride] 평균.

    프레임이 그 블록 샘플에만 의존하므로 어떤 청크 경계로 잘라도 통짜와 동일한 프레임을
    낸다 — 스티칭 산술이 정확하면 청크 결과 == 통짜 결과."""
    t = len(wave) // stride
    means = np.array([wave[j * stride : (j + 1) * stride].mean() for j in range(t)])
    if n_ch == 1:
        return means
    return means[:, None] * (np.arange(n_ch) + 1)


# ── plan / keep 산술 ─────────────────────────────────────────────────────────


def test_single_window_when_disabled_or_fits():
    assert plan_chunk_windows(5000, 0, 100) == [(0, 5000)]  # chunk<=0 → 비활성
    assert plan_chunk_windows(5000, 6000, 100) == [(0, 5000)]  # 오디오가 한 청크에 들어감
    assert plan_chunk_windows(5000, 5000, 100) == [(0, 5000)]  # 정확히 한 청크


def test_plan_windows_tile_and_overlap():
    n, chunk, overlap = 1000, 400, 100
    w = plan_chunk_windows(n, chunk, overlap)
    assert w[0][0] == 0 and w[-1][1] == n
    for a, b in w:
        assert 0 <= a < b <= n
        assert b - a <= chunk
    # 인접 윈도는 overlap만큼 겹친다 (마지막은 n으로 클램프될 수 있음)
    for (a0, b0), (a1, b1) in zip(w, w[1:]):
        assert b0 - a1 == overlap or b1 == n


def test_keep_ranges_tile_contiguously():
    n, chunk, overlap = 1000, 400, 100
    w = plan_chunk_windows(n, chunk, overlap)
    keeps = keep_ranges_for_windows(w, n)
    assert keeps[0][0] == 0 and keeps[-1][1] == n
    for (ks0, ke0), (ks1, ke1) in zip(keeps, keeps[1:]):
        assert ke0 == ks1  # 빈틈·겹침 없이 타일링
    for (s, e), (ks, ke) in zip(w, keeps):
        assert s <= ks <= ke <= e  # 채택 구간은 윈도 안


# ── 스티칭 동등성 ────────────────────────────────────────────────────────────


def test_stitch_matches_whole_exact_when_aligned():
    """청크 경계가 프레임 격자 정렬(overlap = 2*stride 배수)이면 통짜와 완전 동일."""
    rng = np.random.default_rng(0)
    n = 100 * CTC_STRIDE
    wave = rng.standard_normal(n)
    whole = _local_frames(wave, CTC_STRIDE)

    windows = plan_chunk_windows(n, 20 * CTC_STRIDE, 4 * CTC_STRIDE)
    assert len(windows) > 1
    outs = [_local_frames(np.ascontiguousarray(wave[s:e]), CTC_STRIDE) for s, e in windows]
    stitched = stitch_chunk_outputs(outs, windows, n, frame_axis=0)

    assert stitched.shape == whole.shape
    assert np.allclose(stitched, whole)


def test_stitch_torch_tensor_frame_axis1():
    """CTC emission 모양 [1, T, V] (frame_axis=1) torch 경로."""
    import torch

    n = 60 * CTC_STRIDE
    wave = np.arange(n, dtype=np.float64)

    def fake_t(w):
        f = _local_frames(np.ascontiguousarray(w), CTC_STRIDE, n_ch=5)  # [t, V]
        return torch.from_numpy(f).unsqueeze(0)  # [1, t, V]

    whole = fake_t(wave)
    windows = plan_chunk_windows(n, 20 * CTC_STRIDE, 4 * CTC_STRIDE)
    outs = [fake_t(wave[s:e]) for s, e in windows]
    stitched = stitch_chunk_outputs(outs, windows, n, frame_axis=1)

    assert stitched.shape == whole.shape
    assert torch.allclose(stitched, whole)


def test_single_window_is_identity():
    n = 5000
    wave = np.random.default_rng(1).standard_normal(n)
    out = _local_frames(wave, CTC_STRIDE)
    windows = plan_chunk_windows(n, 0, 0)
    stitched = stitch_chunk_outputs([out], windows, n, frame_axis=0)
    assert np.array_equal(stitched, out)


def test_arbitrary_sizes_length_close_to_whole():
    """비정렬(임의 길이)에서도 크래시 없이 길이가 통짜와 ±윈도수 이내로 근접."""
    rng = np.random.default_rng(2)
    for n in [16000, 23117, 480003, 515017]:
        wave = rng.standard_normal(n)
        whole = _local_frames(wave, CTC_STRIDE)
        windows = plan_chunk_windows(n, 20 * CTC_STRIDE, 4 * CTC_STRIDE)
        outs = [_local_frames(np.ascontiguousarray(wave[s:e]), CTC_STRIDE) for s, e in windows]
        stitched = stitch_chunk_outputs(outs, windows, n, frame_axis=0)
        assert abs(stitched.shape[0] - whole.shape[0]) <= len(windows)


# ── 실제 청크 경로 (모델 forward만 합성) ─────────────────────────────────────


def test_ctc_log_emission_chunked_matches_whole(monkeypatch):
    """CTCEngine._ctc_log_emission: 청크 경로 emission == 통짜 emission (모델 forward 합성)."""
    import torch

    from everyric2.alignment.ctc_engine import CTCEngine
    from everyric2.config.settings import AlignmentSettings

    V = 6

    def fake_logits(self, waveform_1d, device):
        f = _local_frames(waveform_1d.numpy().astype(np.float64), CTC_STRIDE, n_ch=V)
        return torch.from_numpy(f.astype(np.float32)).unsqueeze(0)  # [1, t, V]

    monkeypatch.setattr(CTCEngine, "_model_logits", fake_logits)

    n = 80 * CTC_STRIDE
    wave = torch.from_numpy(np.random.default_rng(5).standard_normal(n).astype(np.float32))

    whole = CTCEngine(AlignmentSettings(align_chunk_sec=0.0))._ctc_log_emission(wave, "cpu")
    chunked = CTCEngine(
        AlignmentSettings(
            align_chunk_sec=20 * CTC_STRIDE / 16000,
            align_chunk_overlap_sec=4 * CTC_STRIDE / 16000,
        )
    )._ctc_log_emission(wave, "cpu")

    assert whole.shape == chunked.shape
    assert torch.allclose(whole, chunked, atol=1e-6)


def test_infer_f0_chunked_matches_whole(monkeypatch):
    """MelodyExtractor._infer_f0: 청크 경로 f0 == 통짜 f0 (백엔드 추론 합성)."""
    from everyric2.audio.loader import AudioData
    from everyric2.config.settings import MelodySettings
    from everyric2.melody.extractor import MelodyExtractor

    def fake_chunk(self, wave):
        return _local_frames(np.asarray(wave, dtype=np.float64), F0_HOP)

    monkeypatch.setattr(MelodyExtractor, "_infer_f0_chunk", fake_chunk)

    n = 200 * F0_HOP
    wave = np.random.default_rng(7).standard_normal(n).astype(np.float32)
    audio = AudioData(waveform=wave, sample_rate=16000, duration=n / 16000)

    f0_whole, t_whole = MelodyExtractor(
        MelodySettings(chunk_sec=0.0, separate_vocals=False)
    )._infer_f0(audio)
    f0_chunk, t_chunk = MelodyExtractor(
        MelodySettings(
            chunk_sec=20 * F0_HOP / 16000,
            chunk_overlap_sec=4 * F0_HOP / 16000,
            separate_vocals=False,
        )
    )._infer_f0(audio)

    assert f0_whole.shape == f0_chunk.shape
    assert np.allclose(f0_whole, f0_chunk)
    assert np.allclose(t_whole, t_chunk)
