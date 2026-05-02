"""M6.4 — voice-ducked background-music mix.

Mix a project's uploaded BGM track under the rendered video's audio so
the music drops to ``BGM_VOLUME_DUCKED`` while the speaker is talking
and floats back to ``BGM_VOLUME_BASE`` between cues. Voice-presence
ranges come from the SRT cues the subtitle stage already produced —
no separate VAD pass.

The mix is its own ffmpeg pass after subtitle burn-in so the orchestrator
can mark a ``bgm`` progress step (and so a BGM failure leaves the
subtitled mp4 in place as a usable fallback).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Step-function ducking curve. 0.55 is loud-but-not-clashing for a
# speaking voice mixed at original gain; 0.20 still keeps the BGM
# audible-but-out-of-the-way under voice. Tunable constants — bump
# BASE if speakerphone-quality voices fight with the music, bump
# DUCKED for sparser voice tracks where the music can sit higher.
BGM_VOLUME_BASE: float = 0.55
BGM_VOLUME_DUCKED: float = 0.20

BGM_MIX_TIMEOUT_S: float = 600.0


class BgmMixError(RuntimeError):
    """ffmpeg failed during the BGM mix stage (no fallback inside)."""


def _is_fake() -> bool:
    return os.environ.get("FFMPEG_FAKE", "0") == "1"


def _ts_to_seconds(ts: str) -> float:
    """SRT ``HH:MM:SS,mmm`` → float seconds."""
    h, m, rest = ts.strip().split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_cue_ranges(srt_text: str) -> list[tuple[float, float]]:
    """Extract ``(start_s, end_s)`` from each SRT cue. Bad blocks skipped.

    A simpler sibling of ``video_renderer._parse_srt_cues`` — we don't
    need the cue text here, only the timing for the duck expression.
    """
    out: list[tuple[float, float]] = []
    for raw_block in srt_text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = raw_block.split("\n")
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            a, b = lines[1].split("-->")
            start = _ts_to_seconds(a)
            end = _ts_to_seconds(b)
        except (ValueError, IndexError):
            continue
        if end > start:
            out.append((start, end))
    return out


def _build_duck_expression(cues: list[tuple[float, float]]) -> str:
    """ffmpeg ``volume`` expression: DUCKED during voice cues, BASE between.

    ``+`` in ffmpeg expression syntax acts as logical OR on numerics,
    so summing ``between(t,a,b)`` terms gives 1 if any cue is active.
    """
    if not cues:
        return f"{BGM_VOLUME_BASE}"
    terms = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in cues)
    return f"if({terms},{BGM_VOLUME_DUCKED},{BGM_VOLUME_BASE})"


def mix_bgm(
    video_path: Path,
    bgm_path: Path,
    srt_path: Path | None,
    output_path: Path,
) -> None:
    """Re-encode ``video_path``'s audio with BGM mixed in under voice ducking.

    Video stream is copied (no re-encode). Audio gets re-encoded as AAC
    since we're chaining a filter. ``-shortest`` clips BGM to the video's
    duration so a 4-minute song over a 60-s reel doesn't tail out.
    """
    if shutil.which("ffmpeg") is None and not _is_fake():
        raise BgmMixError("ffmpeg not on PATH")
    if not video_path.is_file() and not _is_fake():
        raise BgmMixError(f"bgm: video missing at {video_path}")
    if not bgm_path.is_file() and not _is_fake():
        raise BgmMixError(f"bgm: bgm file missing at {bgm_path}")

    cues: list[tuple[float, float]] = []
    if srt_path is not None and srt_path.is_file():
        try:
            cues = _parse_cue_ranges(srt_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise BgmMixError(f"bgm: cannot read SRT at {srt_path}: {exc}") from exc

    expr = _build_duck_expression(cues)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(bgm_path),
        "-filter_complex",
        (
            f"[1:a]volume=eval=frame:volume='{expr}'[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        ),
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]

    if _is_fake():
        output_path.write_bytes(b"")
        logger.info("FFMPEG_FAKE=1 — wrote empty bgm mix at %s", output_path)
        return

    try:
        subprocess.run(cmd, check=True, timeout=BGM_MIX_TIMEOUT_S, capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise BgmMixError(f"bgm mix timed out after {BGM_MIX_TIMEOUT_S}s") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise BgmMixError(f"bgm mix ffmpeg failed: {stderr[:500]}") from exc

    logger.info("bgm mix: %d voice cues, output=%s", len(cues), output_path)


__all__ = [
    "BGM_MIX_TIMEOUT_S",
    "BGM_VOLUME_BASE",
    "BGM_VOLUME_DUCKED",
    "BgmMixError",
    "mix_bgm",
]
