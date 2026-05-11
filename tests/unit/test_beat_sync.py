from __future__ import annotations

import pytest

from media_processor.services import beat_sync


def test_estimate_beat_grid_from_pcm_detects_regular_pulses() -> None:
    np = pytest.importorskip("numpy")
    sample_rate = beat_sync.BEAT_SAMPLE_RATE
    duration_s = 8.0
    samples = np.zeros(int(sample_rate * duration_s), dtype=np.float32)
    pulse_len = int(sample_rate * 0.025)
    for beat_idx in range(1, int(duration_s / 0.5)):
        start = int(beat_idx * 0.5 * sample_rate)
        samples[start : start + pulse_len] = 1.0

    analysis = beat_sync.estimate_beat_grid_from_pcm(
        samples.astype("<f4").tobytes(),
        sample_rate=sample_rate,
        duration_s=duration_s,
    )

    assert analysis.bpm is not None
    assert 110.0 <= analysis.bpm <= 130.0
    assert len(analysis.beats_s) >= 8
    intervals = [b - a for a, b in zip(analysis.beats_s, analysis.beats_s[1:], strict=False)]
    assert intervals
    assert 0.45 <= sum(intervals[:5]) / min(5, len(intervals)) <= 0.55
