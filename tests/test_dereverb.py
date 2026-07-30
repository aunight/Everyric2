"""Pitch-only WPE dereverberation tests."""

import builtins
import sys
from types import ModuleType

import numpy as np
import pytest


def test_missing_nara_wpe_fails_clearly(monkeypatch):
    from everyric2.audio.dereverb import (
        DereverbUnavailableError,
        dereverb_for_pitch,
    )

    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("nara_wpe"):
            raise ImportError("nara_wpe intentionally unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(DereverbUnavailableError, match=r"\[dereverb\]"):
        dereverb_for_pitch(np.zeros(1600, dtype=np.float32), 16000)


def test_wpe_output_is_finite_float32_and_keeps_the_input_length(monkeypatch):
    from everyric2.audio.dereverb import dereverb_for_pitch

    original = np.linspace(-0.5, 0.5, 4096, dtype=np.float32)
    untouched = original.copy()
    captured: dict[str, object] = {}

    utils = ModuleType("nara_wpe.utils")
    utils.stft = lambda waveform, **_kwargs: np.zeros(
        (waveform.shape[0], 8, 257),
        dtype=np.complex128,
    )
    utils.istft = lambda _spectrum, **_kwargs: np.concatenate(
        [
            np.full((1, 800), np.nan),
            np.full((1, 900), 0.25),
        ],
        axis=1,
    )

    wpe_module = ModuleType("nara_wpe.wpe")

    def fake_wpe(spectrum, *, taps, delay, iterations, statistics_mode):
        captured.update(
            {
                "shape": spectrum.shape,
                "taps": taps,
                "delay": delay,
                "iterations": iterations,
                "statistics_mode": statistics_mode,
            }
        )
        return spectrum

    wpe_module.wpe = fake_wpe
    monkeypatch.setitem(sys.modules, "nara_wpe", ModuleType("nara_wpe"))
    monkeypatch.setitem(sys.modules, "nara_wpe.utils", utils)
    monkeypatch.setitem(sys.modules, "nara_wpe.wpe", wpe_module)

    result = dereverb_for_pitch(
        original,
        16000,
        taps=8,
        delay=2,
        iterations=2,
    )

    assert result.dtype == np.float32
    assert result.shape == original.shape
    assert np.isfinite(result).all()
    assert captured == {
        "shape": (257, 1, 8),
        "taps": 8,
        "delay": 2,
        "iterations": 2,
        "statistics_mode": "full",
    }
    assert np.array_equal(original, untouched)
