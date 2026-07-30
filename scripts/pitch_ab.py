#!/usr/bin/env python3
"""Compare Everyric2 f0 backends and optional pitch-only dereverberation."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Literal

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import median_filter

from everyric2.audio.loader import AudioData, AudioLoader
from everyric2.config.settings import MelodySettings
from everyric2.melody.extractor import MelodyExtractor, hz_to_midi

PitchBackend = Literal["rmvpe", "fcpe"]


def pitch_track_metrics(f0: NDArray[np.floating]) -> dict[str, float | int]:
    """Return reference-free stability metrics for one uniformly sampled f0 track."""
    values = np.asarray(f0, dtype=np.float64).reshape(-1)
    frames = int(values.size)
    voiced = np.isfinite(values) & (values > 0)
    voiced_count = int(voiced.sum())
    if frames == 0 or voiced_count == 0:
        return {
            "frames": frames,
            "voiced_ratio": 0.0,
            "adjacent_voiced_pairs": 0,
            "large_jump_ratio": 0.0,
            "local_subharmonic_ratio": 0.0,
        }

    midi = hz_to_midi(values)
    adjacent = voiced[:-1] & voiced[1:]
    adjacent_count = int(adjacent.sum())
    jumps = np.abs(np.diff(midi))
    large_jump_ratio = (
        float((jumps[adjacent] > 7.0).mean())
        if adjacent_count
        else 0.0
    )

    indices = np.arange(frames)
    interpolated = np.interp(indices, indices[voiced], midi[voiced])
    window = min(201, frames if frames % 2 == 1 else max(1, frames - 1))
    local_center = median_filter(interpolated, size=window, mode="nearest")
    offset = midi[voiced] - local_center[voiced]
    local_subharmonic_ratio = float(
        ((offset >= -14.0) & (offset <= -10.0)).mean()
    )

    return {
        "frames": frames,
        "voiced_ratio": float(voiced_count / frames),
        "adjacent_voiced_pairs": adjacent_count,
        "large_jump_ratio": large_jump_ratio,
        "local_subharmonic_ratio": local_subharmonic_ratio,
    }


def trim_audio(audio: AudioData, max_seconds: float) -> AudioData:
    if max_seconds <= 0 or audio.duration <= max_seconds:
        return audio
    samples = int(round(max_seconds * audio.sample_rate))
    waveform = np.ascontiguousarray(audio.waveform[:samples])
    return AudioData(
        waveform=waveform,
        sample_rate=audio.sample_rate,
        duration=len(waveform) / audio.sample_rate,
        source_path=audio.source_path,
    )


def benchmark(
    audio_path: Path,
    *,
    backend: PitchBackend,
    dereverb: bool,
    device: str,
    max_seconds: float,
    separate_vocals: bool,
) -> dict[str, object]:
    audio = trim_audio(AudioLoader().load(audio_path), max_seconds)
    config = MelodySettings(
        f0_model=backend,
        dereverb=dereverb,
        device=device,
        separate_vocals=separate_vocals,
    )
    extractor = MelodyExtractor(config)
    started = time.perf_counter()
    f0, _times = extractor.precompute_f0(audio)
    elapsed = time.perf_counter() - started

    return {
        "audio": str(audio_path),
        "duration_sec": audio.duration,
        "requested_backend": backend,
        "actual_backend": extractor._backend,
        "device": device,
        "dereverb": dereverb,
        "separate_vocals": separate_vocals,
        "elapsed_sec": elapsed,
        **pitch_track_metrics(f0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", nargs="+", type=Path)
    parser.add_argument(
        "--backend",
        action="append",
        choices=("rmvpe", "fcpe"),
        dest="backends",
        help="Backend to run; repeat for both (default: rmvpe and fcpe).",
    )
    parser.add_argument("--compare-dereverb", action="store_true")
    parser.add_argument("--separate-vocals", action="store_true")
    parser.add_argument("--device", default="auto")
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=30.0,
        help="Analyze only the first N seconds of each file; 0 keeps the full file.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    backends: list[PitchBackend] = args.backends or ["rmvpe", "fcpe"]
    dereverb_modes = [False, True] if args.compare_dereverb else [False]
    results: list[dict[str, object]] = []
    for audio_path in args.audio:
        for backend in backends:
            for dereverb in dereverb_modes:
                result = benchmark(
                    audio_path,
                    backend=backend,
                    dereverb=dereverb,
                    device=args.device,
                    max_seconds=args.max_seconds,
                    separate_vocals=args.separate_vocals,
                )
                results.append(result)
                print(json.dumps(result, ensure_ascii=False))

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
