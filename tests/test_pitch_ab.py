"""A/B pitch diagnostic metric tests."""

import numpy as np

from scripts.pitch_ab import pitch_track_metrics


def test_pitch_metrics_count_voicing_and_large_jumps():
    f0 = np.array(
        [
            440.0,
            440.0,
            0.0,
            440.0,
            880.0,
            440.0,
        ],
        dtype=np.float64,
    )

    metrics = pitch_track_metrics(f0)

    assert metrics["frames"] == 6
    assert metrics["voiced_ratio"] == 5 / 6
    assert metrics["adjacent_voiced_pairs"] == 3
    assert metrics["large_jump_ratio"] == 2 / 3


def test_pitch_metrics_handle_empty_and_unvoiced_tracks():
    assert pitch_track_metrics(np.array([], dtype=np.float64)) == {
        "frames": 0,
        "voiced_ratio": 0.0,
        "adjacent_voiced_pairs": 0,
        "large_jump_ratio": 0.0,
        "local_subharmonic_ratio": 0.0,
    }
    assert pitch_track_metrics(np.zeros(20, dtype=np.float64))["voiced_ratio"] == 0.0
