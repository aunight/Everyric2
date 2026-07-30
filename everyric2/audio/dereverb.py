"""Optional WPE dereverberation for the melody f0 branch only."""

from __future__ import annotations

import logging

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


class DereverbUnavailableError(RuntimeError):
    """Raised when pitch dereverberation is enabled without its dependency."""


def dereverb_for_pitch(
    waveform: NDArray[np.floating],
    sample_rate: int,
    *,
    taps: int = 10,
    delay: int = 3,
    iterations: int = 3,
    fft_size: int = 512,
    hop_length: int = 128,
) -> NDArray[np.float32]:
    """Run offline mono WPE and return a finite, length-preserving float32 copy."""
    try:
        from nara_wpe.utils import istft, stft
        from nara_wpe.wpe import wpe
    except ImportError as exc:
        raise DereverbUnavailableError(
            "Pitch dereverb requires NARA-WPE. "
            'Install it with `pip install -e ".[dereverb]"`.'
        ) from exc

    source: NDArray[np.float64] = np.asarray(waveform, dtype=np.float64)
    if source.ndim != 1:
        raise ValueError(f"Pitch dereverb expects mono audio, received shape {source.shape}")
    if sample_rate <= 0:
        raise ValueError(f"Invalid sample rate: {sample_rate}")
    if source.size == 0:
        return np.empty(0, dtype=np.float32)

    minimum_samples = fft_size + hop_length * (taps + delay)
    if source.size < minimum_samples:
        logger.info(
            "Pitch dereverb skipped: %d samples is shorter than the WPE minimum %d",
            source.size,
            minimum_samples,
        )
        return np.ascontiguousarray(source, dtype=np.float32)

    observed = stft(
        source[None, :],
        size=fft_size,
        shift=hop_length,
    ).transpose(2, 0, 1)
    enhanced = wpe(
        observed,
        taps=taps,
        delay=delay,
        iterations=iterations,
        statistics_mode="full",
    )
    restored: NDArray[np.float64] = np.asarray(
        istft(
            enhanced.transpose(1, 2, 0),
            size=fft_size,
            shift=hop_length,
        ),
        dtype=np.float64,
    )
    if restored.ndim == 2:
        restored = restored[0]
    restored = restored.reshape(-1)
    restored = np.nan_to_num(restored, nan=0.0, posinf=0.0, neginf=0.0)

    output = np.zeros(source.size, dtype=np.float32)
    copied = min(source.size, restored.size)
    output[:copied] = restored[:copied].astype(np.float32, copy=False)
    return np.ascontiguousarray(output, dtype=np.float32)
