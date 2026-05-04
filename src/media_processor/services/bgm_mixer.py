"""M6.4 — voice-ducked background-music mix.

Mix a project's uploaded BGM track under the rendered video's audio so
the music drops to ``BGM_VOLUME_DUCKED`` while the speaker is talking
and floats back to ``BGM_VOLUME_BASE`` between cues. Voice-presence
ranges come from the SRT cues the subtitle stage already produced —
no separate VAD pass.

v0.17 added optional per-segment overrides: callers can supply a list
of ``SegmentVolume(start_s, end_s, voice_volume, bgm_volume)`` tuples
and the filter chain will apply those gain expressions inside each
segment's timeline window. ``voice_volume`` scales the source audio;
``bgm_volume`` overrides the auto-ducking expression for that window
(``None`` = let the duck curve run inside the segment as usual).

The mix is its own ffmpeg pass after subtitle burn-in so the orchestrator
can mark a ``bgm`` progress step (and so a BGM failure leaves the
subtitled mp4 in place as a usable fallback).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SegmentVolume:
    """v0.17 — per-segment audio gain overrides.

    Times are in *output* (rendered timeline) seconds, matching what
    SRT cues use. ``voice_volume`` scales ``[0:a]`` in this window;
    ``bgm_volume`` overrides the auto-duck expression on ``[1:a]``.
    ``None`` for ``bgm_volume`` means "let the auto duck continue
    inside this window" (useful when only voice volume changes).
    """

    start_s: float
    end_s: float
    voice_volume: float = 1.0
    bgm_volume: float | None = None

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


def _build_voice_volume_expr(segments: list[SegmentVolume]) -> str:
    """Stepped ffmpeg ``volume`` expression for the voice track.

    Returns ``"1.0"`` when no segment overrides exist (mixer is a no-op).
    Otherwise builds nested ``if(between(t,…),v_i,…)`` so each segment
    window applies its own gain; gaps between segments fall through to
    1.0 (original gain).
    """
    if not segments:
        return "1.0"
    expr = "1.0"
    for seg in reversed(segments):
        if seg.voice_volume == 1.0:
            continue
        expr = (
            f"if(between(t,{seg.start_s:.3f},{seg.end_s:.3f}),"
            f"{seg.voice_volume:.3f},{expr})"
        )
    return expr


def _build_bgm_volume_expr(
    cues: list[tuple[float, float]],
    segments: list[SegmentVolume],
) -> str:
    """Compose the BGM gain expression with optional per-segment overrides.

    Outside any overriding segment the auto duck curve from
    :func:`_build_duck_expression` rules. Inside a segment whose
    ``bgm_volume`` is set the override pins the gain; segments with
    ``bgm_volume=None`` keep the auto duck.
    """
    duck = _build_duck_expression(cues)
    overrides = [s for s in segments if s.bgm_volume is not None]
    if not overrides:
        return duck
    expr = duck
    for seg in reversed(overrides):
        # Mypy needs the assert; logically guarded by the filter above.
        assert seg.bgm_volume is not None
        expr = (
            f"if(between(t,{seg.start_s:.3f},{seg.end_s:.3f}),"
            f"{seg.bgm_volume:.3f},{expr})"
        )
    return expr


def _probe_video_duration_s(video_path: Path) -> float | None:
    """Best-effort ``ffprobe`` of a video's container duration in seconds.

    Used by :func:`mix_bgm` to compute the start time of the BGM tail
    fade. Returns ``None`` on any failure so the caller can skip the
    fade gracefully (rather than fail the whole render). Not worth
    its own module — it's only ever called from one place.
    """
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    raw = result.stdout.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def mix_bgm(
    video_path: Path,
    bgm_path: Path,
    srt_path: Path | None,
    output_path: Path,
    *,
    segments: list[SegmentVolume] | None = None,
    fade_out_sec: float = 0.0,
) -> None:
    """Re-encode ``video_path``'s audio with BGM mixed in under voice ducking.

    Video stream is copied (no re-encode). Audio gets re-encoded as AAC
    since we're chaining a filter. ``-shortest`` clips BGM to the video's
    duration so a 4-minute song over a 60-s reel doesn't tail out.

    ``segments`` (v0.17) optionally adds per-segment voice / BGM gain
    overrides. ``None`` (or empty list) keeps the M6.4 behaviour: voice
    plays at original gain, BGM follows the auto-duck curve.

    ``fade_out_sec`` (v0.24.0) appends ``afade=t=out`` to the BGM
    track so the music tapers into silence over the last N seconds.
    ``0.0`` (default) keeps the pre-0.24.0 hard-cut behaviour. The
    fade is on the BGM track only — voice already gets cut by
    ``-shortest`` matching the video, so fading voice would just
    pre-empt the operator's choice of where the speech ends. Probes
    the video duration via ``ffprobe`` to compute the fade start
    offset; on probe failure the fade is silently skipped (the mix
    still ships, just without the taper).
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

    seg_list: list[SegmentVolume] = list(segments or [])
    voice_expr = _build_voice_volume_expr(seg_list)
    bgm_expr = _build_bgm_volume_expr(cues, seg_list)

    # v0.24.0 — compose the BGM tail-fade tail. Skip the fade when
    # the duration probe fails (no point starting at NaN) or when
    # the requested fade is zero / longer than the video.
    bgm_fade_filter = ""
    if fade_out_sec > 0.0:
        duration_s = _probe_video_duration_s(video_path) if not _is_fake() else None
        if duration_s is not None and duration_s > 0.0:
            fade_dur = min(fade_out_sec, duration_s)
            fade_start = max(0.0, duration_s - fade_dur)
            bgm_fade_filter = f",afade=t=out:st={fade_start:.3f}:d={fade_dur:.3f}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # v0.21.5 — ``-stream_loop -1`` on the BGM input loops the source
    # track until ffmpeg's output duration limit kicks in. Combined with
    # ``amix=duration=first`` (= match the voice track, which is the
    # full video) and the trailing ``-shortest`` (clipped to the input
    # video), this guarantees the BGM covers the whole runtime even
    # when the source wav is shorter than the video. MusicGen ships
    # 30 s clips by default and operators have asked for arbitrary-
    # length videos; without the loop the back half of the reel went
    # silent.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-stream_loop",
        "-1",
        "-i",
        str(bgm_path),
        "-filter_complex",
        (
            f"[0:a]volume=eval=frame:volume='{voice_expr}'[voice];"
            f"[1:a]volume=eval=frame:volume='{bgm_expr}'{bgm_fade_filter}[bgm];"
            f"[voice][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]"
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


def apply_voice_volume(
    video_path: Path,
    output_path: Path,
    segments: list[SegmentVolume],
) -> None:
    """Re-encode the audio track applying per-segment voice gain.

    Used when the project has no BGM but the user set per-segment voice
    overrides — keeps the audio chain consistent without forcing a BGM
    file. No-op (file copy) when no segment carries a non-default
    ``voice_volume``.
    """
    if shutil.which("ffmpeg") is None and not _is_fake():
        raise BgmMixError("ffmpeg not on PATH")
    if not video_path.is_file() and not _is_fake():
        raise BgmMixError(f"voice-mix: video missing at {video_path}")

    expr = _build_voice_volume_expr(segments)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if expr == "1.0":
        # Nothing to do — caller can skip; we still produce the output
        # path for consistency by copying the file.
        if _is_fake():
            output_path.write_bytes(b"")
            return
        import shutil as _shutil
        _shutil.copyfile(video_path, output_path)
        return

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-filter:a",
        f"volume=eval=frame:volume='{expr}'",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    if _is_fake():
        output_path.write_bytes(b"")
        return
    try:
        subprocess.run(cmd, check=True, timeout=BGM_MIX_TIMEOUT_S, capture_output=True)
    except subprocess.TimeoutExpired as exc:
        raise BgmMixError(f"voice-mix timed out after {BGM_MIX_TIMEOUT_S}s") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise BgmMixError(f"voice-mix ffmpeg failed: {stderr[:500]}") from exc


__all__ = [
    "BGM_MIX_TIMEOUT_S",
    "BGM_VOLUME_BASE",
    "BGM_VOLUME_DUCKED",
    "BgmMixError",
    "SegmentVolume",
    "apply_voice_volume",
    "mix_bgm",
]
