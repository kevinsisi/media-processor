"""Lightweight BGM beat-grid analysis for render-time camera sync.

The editing worker already ships ffmpeg + numpy, so keep this deliberately
small instead of adding librosa. The output is a best-effort beat grid in
render timeline seconds; callers can ignore an empty result and keep the
existing non-synced camera curve.
"""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


BEAT_SAMPLE_RATE: int = 11_025
BEAT_MIN_BPM: float = 70.0
BEAT_MAX_BPM: float = 180.0
BEAT_ANALYSIS_TIMEOUT_S: float = 30.0


@dataclass(frozen=True)
class BeatAnalysis:
    bpm: float | None
    beats_s: list[float]


def analyze_bgm_beats(
    bgm_path: Path,
    *,
    duration_s: float,
) -> BeatAnalysis:
    """Decode ``bgm_path`` and return a looped beat grid for ``duration_s``.

    ``bgm_mixer`` loops short BGM tracks under longer videos, so this decode
    uses the same ``-stream_loop -1`` shape and limits output to the target
    render duration. Failures are non-fatal: Smart Camera simply falls back to
    its visual-motion ease when no reliable beat grid is available.
    """
    bgm_path = Path(bgm_path)
    duration_s = max(1.0, float(duration_s))
    if shutil.which("ffmpeg") is None:
        logger.warning("beat-sync: ffmpeg not on PATH; skipping beat analysis")
        return BeatAnalysis(bpm=None, beats_s=[])
    if not bgm_path.is_file():
        logger.warning("beat-sync: BGM missing at %s; skipping beat analysis", bgm_path)
        return BeatAnalysis(bpm=None, beats_s=[])

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path),
        "-t",
        f"{duration_s:.3f}",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(BEAT_SAMPLE_RATE),
        "-f",
        "f32le",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            timeout=BEAT_ANALYSIS_TIMEOUT_S,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        logger.warning("beat-sync: ffmpeg decode failed for %s: %s", bgm_path, exc)
        return BeatAnalysis(bpm=None, beats_s=[])

    return estimate_beat_grid_from_pcm(
        proc.stdout,
        sample_rate=BEAT_SAMPLE_RATE,
        duration_s=duration_s,
    )


def estimate_beat_grid_from_pcm(
    pcm_f32le: bytes,
    *,
    sample_rate: int,
    duration_s: float,
) -> BeatAnalysis:
    """Estimate a beat grid from mono float32 PCM bytes.

    The algorithm is intentionally conservative: build an RMS onset envelope,
    find the strongest tempo autocorrelation in a bounded BPM range, then pick
    the phase whose periodic grid hits the most onset energy.
    """
    if not pcm_f32le:
        return BeatAnalysis(bpm=None, beats_s=[])

    try:
        import numpy as np
    except ImportError:
        logger.warning("beat-sync: numpy unavailable; skipping beat analysis")
        return BeatAnalysis(bpm=None, beats_s=[])

    samples = np.frombuffer(pcm_f32le, dtype="<f4")
    if samples.size < sample_rate // 2:
        return BeatAnalysis(bpm=None, beats_s=[])
    samples = np.nan_to_num(samples.astype(np.float32, copy=False))

    hop = max(1, int(round(sample_rate * 0.046)))
    frame = max(hop * 2, int(round(sample_rate * 0.092)))
    frame_count = 1 + max(0, (samples.size - frame) // hop)
    if frame_count < 8:
        return BeatAnalysis(bpm=None, beats_s=[])

    envelope = np.empty(frame_count, dtype=np.float32)
    for i in range(frame_count):
        chunk = samples[i * hop : i * hop + frame]
        envelope[i] = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
    if float(np.max(envelope)) < 1e-4:
        return BeatAnalysis(bpm=None, beats_s=[])

    onset = np.maximum(0.0, np.diff(np.log1p(envelope * 10.0), prepend=envelope[0]))
    onset = onset - float(np.median(onset))
    onset = np.maximum(0.0, onset)
    if float(np.max(onset)) <= 1e-6:
        return BeatAnalysis(bpm=None, beats_s=[])

    hop_s = hop / float(sample_rate)
    min_lag = max(1, int(round((60.0 / BEAT_MAX_BPM) / hop_s)))
    max_lag = max(min_lag, int(round((60.0 / BEAT_MIN_BPM) / hop_s)))
    max_lag = min(max_lag, len(onset) - 2)
    if max_lag <= min_lag:
        return BeatAnalysis(bpm=None, beats_s=[])

    centered = onset - float(np.mean(onset))
    best_lag: int | None = None
    best_score = -math.inf
    for lag in range(min_lag, max_lag + 1):
        score = float(np.dot(centered[lag:], centered[:-lag]))
        if score > best_score:
            best_score = score
            best_lag = lag
    if best_lag is None or best_score <= 0.0:
        return BeatAnalysis(bpm=None, beats_s=[])

    best_offset = max(
        range(best_lag),
        key=lambda offset: float(np.sum(onset[offset::best_lag])),
    )
    period_s = best_lag * hop_s
    bpm = 60.0 / period_s if period_s > 0 else None
    if bpm is None or bpm < BEAT_MIN_BPM * 0.85 or bpm > BEAT_MAX_BPM * 1.15:
        return BeatAnalysis(bpm=None, beats_s=[])

    first = best_offset * hop_s
    while first - period_s >= 0.0:
        first -= period_s
    beats: list[float] = []
    t = max(0.0, first)
    limit = max(duration_s, 0.0) + 0.001
    while t <= limit:
        beats.append(round(t, 3))
        t += period_s
    return BeatAnalysis(bpm=round(bpm, 2), beats_s=beats)


__all__ = [
    "BEAT_ANALYSIS_TIMEOUT_S",
    "BEAT_MAX_BPM",
    "BEAT_MIN_BPM",
    "BEAT_SAMPLE_RATE",
    "BeatAnalysis",
    "analyze_bgm_beats",
    "estimate_beat_grid_from_pcm",
]
