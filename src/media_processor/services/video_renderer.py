"""FFmpeg-driven cut + concat + subtitle burn pipeline for M5 auto-edit.

Three sub-stages run in sequence: per-segment cut + scale-and-crop +
re-encode → concat-demuxer mux → subtitle burn-in. Each stage is its own
ffmpeg subprocess call so failures are localised and the worker can mark
the right step in ``Draft.progress_steps_json``.

The renderer is the only M5 module that shells out to ffmpeg for editing
work; ``services.thumbnails`` shares ffmpeg but stays scoped to keyframe
extraction.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from media_processor.services.edit_planner import CutPlan, CutPlanSegment

logger = logging.getLogger(__name__)


# Output dimensions per target aspect ratio. 1080-wide for the portrait
# variants is the IG / TikTok native upload size; using a fixed width
# keeps the per-segment scale + crop deterministic.
ASPECT_DIMENSIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
}

# Per-segment encoding knobs. CRF 20 + libx264 + faststart matches the
# IG upload spec; AAC 128 k mono/stereo is the smallest sane default.
VIDEO_CODEC: str = "libx264"
VIDEO_PIX_FMT: str = "yuv420p"
VIDEO_PRESET: str = "veryfast"
VIDEO_CRF: int = 20
VIDEO_FPS: int = 30
AUDIO_CODEC: str = "aac"
AUDIO_BITRATE: str = "128k"

# Subtitle burn-in style — white text + 2 px black edge + bottom-centre.
# Sizes/margins below are interpreted in canvas pixels because
# ``burn_subtitles`` sets ``original_size=WxH`` on the ffmpeg
# ``subtitles=`` filter (otherwise libass would scale from its 384×288
# default and CJK lines overflow the side of portrait video).
def subtitle_force_style(target_aspect: str) -> str:
    """Aspect-aware ASS V4+ Style overrides for the subtitle burn-in.

    Tighter Fontsize and explicit horizontal margins keep CJK text inside
    the frame on 9:16; the same numbers tracked the visible width on the
    landscape variants too. Returns a comma-separated string suitable for
    ffmpeg's ``force_style=`` value.
    """
    width, _ = ASPECT_DIMENSIONS[target_aspect]
    if target_aspect == "9:16":
        font_size = 28
        margin_v = 180
    elif target_aspect == "4:5":
        font_size = 26
        margin_v = 120
    else:  # "1:1"
        font_size = 24
        margin_v = 80
    margin_lr = 60
    return (
        "FontName=Noto Sans CJK TC,"
        f"Fontsize={font_size},"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=0,"
        "Alignment=2,"
        f"MarginL={margin_lr},"
        f"MarginR={margin_lr},"
        f"MarginV={margin_v},"
        "WrapStyle=0"
    )


# Default 9:16 style — kept for legacy imports. New code should call
# ``subtitle_force_style(target_aspect)`` and pass the result into the
# subtitles filter.
SUBTITLE_FORCE_STYLE: str = subtitle_force_style("9:16")

# Timeouts. Per-call covers a single ffmpeg invocation; the worker job
# layers its own outer cap on the whole render.
PER_SEGMENT_TIMEOUT_S: float = 300.0
CONCAT_TIMEOUT_S: float = 300.0
SUBTITLE_BURN_TIMEOUT_S: float = 600.0


class VideoRenderError(RuntimeError):
    """Generic ffmpeg failure during the M5 render pipeline."""


class VideoRenderTimeoutError(VideoRenderError):
    """Any of the three stages exceeded its hard cap."""


class FFmpegMissingError(VideoRenderError):
    """ffmpeg binary is not on PATH (worker container is misconfigured)."""


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    intermediate_dir: Path
    segment_count: int
    used_subtitles: bool


# ---------- helpers ----------


def _is_fake() -> bool:
    """True when FFMPEG_FAKE=1 — tests stub the binary so CI can drive
    the planner → renderer → DB happy path without touching ffmpeg."""
    return os.environ.get("FFMPEG_FAKE", "0") == "1"


def _require_ffmpeg() -> None:
    if _is_fake():
        return
    if shutil.which("ffmpeg") is None:
        raise FFmpegMissingError("ffmpeg not on PATH")


def aspect_filter(target_aspect: str) -> str:
    """Return the ``scale=…,crop=…,setsar=1`` filter chain for the target."""
    if target_aspect not in ASPECT_DIMENSIONS:
        raise VideoRenderError(f"unsupported target aspect ratio: {target_aspect!r}")
    width, height = ASPECT_DIMENSIONS[target_aspect]
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        "setsar=1"
    )


def _run(cmd: list[str], *, timeout_s: float, stage: str) -> None:
    """Run ffmpeg with capture; raise descriptive errors on failure."""
    if _is_fake():
        # Tests rely on the *.mp4 path being a real (empty) file so
        # downstream stages can read its existence.
        out_idx = _find_output_path(cmd)
        if out_idx is not None:
            Path(cmd[out_idx]).parent.mkdir(parents=True, exist_ok=True)
            Path(cmd[out_idx]).write_bytes(b"")
        return
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout_s, check=True)
    except subprocess.TimeoutExpired as exc:
        raise VideoRenderTimeoutError(f"{stage} timed out after {timeout_s}s") from exc
    except FileNotFoundError as exc:
        raise FFmpegMissingError(str(exc)) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise VideoRenderError(
            f"{stage} failed (exit {exc.returncode}); ffmpeg stderr: {stderr[:500]}"
        ) from exc


def _find_output_path(cmd: list[str]) -> int | None:
    """Last-arg heuristic: ffmpeg's output path is the final positional arg."""
    if not cmd:
        return None
    return len(cmd) - 1


# ---------- stage 1: per-segment cut + scale + re-encode ----------


def _cut_segment(
    src: Path,
    cut: CutPlanSegment,
    out_path: Path,
    target_aspect: str,
) -> None:
    """Cut + scale-and-crop one segment to a uniform intermediate mp4."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    start_s = cut.asset_start_ms / 1000.0
    duration_s = max(0.001, (cut.asset_end_ms - cut.asset_start_ms) / 1000.0)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_s:.3f}",
        "-i",
        str(src),
        "-t",
        f"{duration_s:.3f}",
        "-vf",
        aspect_filter(target_aspect),
        "-r",
        str(VIDEO_FPS),
        "-c:v",
        VIDEO_CODEC,
        "-pix_fmt",
        VIDEO_PIX_FMT,
        "-preset",
        VIDEO_PRESET,
        "-crf",
        str(VIDEO_CRF),
        "-c:a",
        AUDIO_CODEC,
        "-b:a",
        AUDIO_BITRATE,
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    _run(cmd, timeout_s=PER_SEGMENT_TIMEOUT_S, stage=f"cut(seg={cut.order})")


def cut_segments(
    plan: CutPlan,
    asset_paths: dict[int, Path],
    intermediate_dir: Path,
    target_aspect: str,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Cut every segment in the plan; return the intermediate paths in order."""
    _require_ffmpeg()
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []
    total = len(plan.segments)
    for cut in plan.segments:
        src = asset_paths.get(cut.asset_id)
        if src is None or not Path(src).is_file():
            raise VideoRenderError(f"segment {cut.order}: asset {cut.asset_id} source missing")
        out = intermediate_dir / f"seg_{cut.order:04d}.mp4"
        _cut_segment(Path(src), cut, out, target_aspect)
        out_paths.append(out)
        if on_progress is not None:
            on_progress(cut.order + 1, total)
    return out_paths


# ---------- stage 2: concat ----------


def _write_concat_list(intermediate_paths: list[Path], list_path: Path) -> None:
    """Write the ffmpeg concat-demuxer file list."""
    list_path.parent.mkdir(parents=True, exist_ok=True)
    with list_path.open("w", encoding="utf-8") as fh:
        for p in intermediate_paths:
            # ffmpeg concat demuxer needs forward slashes even on Windows
            # and single-quoted paths to handle spaces.
            posix = str(p).replace("\\", "/")
            fh.write(f"file '{posix}'\n")


def concat_segments(
    intermediate_paths: list[Path],
    output_path: Path,
    list_path: Path,
) -> None:
    """Concat the intermediates into a single mux-only mp4."""
    _require_ffmpeg()
    if not intermediate_paths:
        raise VideoRenderError("concat: no intermediate segments to join")
    _write_concat_list(intermediate_paths, list_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run(cmd, timeout_s=CONCAT_TIMEOUT_S, stage="concat")


# ---------- stage 3: subtitle burn-in ----------


def burn_subtitles(
    concat_path: Path,
    srt_path: Path,
    output_path: Path,
    target_aspect: str = "9:16",
) -> None:
    """Re-encode ``concat_path`` with the SRT burned in via subtitles= filter.

    A separate stage from the concat mux so failures here can be retried
    without redoing the cut work. ``target_aspect`` selects the per-canvas
    style (Fontsize / MarginL / MarginR / MarginV) so portrait CJK lines
    wrap within the frame instead of overflowing the side.
    """
    _require_ffmpeg()
    if target_aspect not in ASPECT_DIMENSIONS:
        raise VideoRenderError(f"unsupported target aspect ratio: {target_aspect!r}")
    if not concat_path.is_file() and not _is_fake():
        raise VideoRenderError(f"burn: concat output missing at {concat_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # ffmpeg subtitle filter on Windows demands escaped colons and forward
    # slashes inside the filter string.
    posix_srt = str(srt_path).replace("\\", "/").replace(":", "\\:")
    width, height = ASPECT_DIMENSIONS[target_aspect]
    style = subtitle_force_style(target_aspect)
    # original_size= forces libass to interpret ASS PlayRes as the actual
    # output resolution, so Fontsize/Margins above are pixel-accurate.
    sub_filter = (
        f"subtitles={posix_srt}:original_size={width}x{height}:force_style='{style}'"
    )
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(concat_path),
        "-vf",
        sub_filter,
        "-c:v",
        VIDEO_CODEC,
        "-pix_fmt",
        VIDEO_PIX_FMT,
        "-preset",
        VIDEO_PRESET,
        "-crf",
        str(VIDEO_CRF),
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run(cmd, timeout_s=SUBTITLE_BURN_TIMEOUT_S, stage="subtitles")


# ---------- top-level orchestrator ----------


def render(
    plan: CutPlan,
    *,
    draft_id: int,
    target_aspect: str,
    asset_paths: dict[int, Path],
    output_path: Path,
    srt_path: Path | None,
    scratch_dir: Path,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> RenderResult:
    """Run the three render stages end-to-end.

    ``on_progress(stage, done, total)`` fires after each stage advance —
    the worker uses it to update ``Draft.progress_steps_json``. ``stage``
    is one of ``"cut" | "concat" | "subtitles"``.
    """
    _require_ffmpeg()

    intermediate_dir = scratch_dir / f"draft_{draft_id}"
    intermediate_dir.mkdir(parents=True, exist_ok=True)

    def _seg_progress(done: int, total: int) -> None:
        if on_progress is not None:
            on_progress("cut", done, total)

    # Stage 1.
    intermediates = cut_segments(
        plan,
        asset_paths,
        intermediate_dir,
        target_aspect,
        on_progress=_seg_progress,
    )

    # Stage 2 — concat into the final output path. If we're going to burn
    # subtitles we still concat first so a subtitle failure leaves a
    # playable preview behind.
    list_path = intermediate_dir / "concat.txt"
    concat_path = intermediate_dir / "concat.mp4" if srt_path is not None else output_path
    concat_segments(intermediates, concat_path, list_path)
    if on_progress is not None:
        on_progress("concat", 1, 1)

    used_subs = False
    if srt_path is not None and srt_path.is_file() and srt_path.stat().st_size > 0:
        burn_subtitles(concat_path, srt_path, output_path, target_aspect)
        used_subs = True
    elif srt_path is not None:
        # No subtitle file produced (transcript-less project); fall back
        # to copying the concat output to the final path.
        if concat_path != output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(concat_path, output_path)
    if on_progress is not None:
        on_progress("subtitles", 1, 1)

    return RenderResult(
        output_path=output_path,
        intermediate_dir=intermediate_dir,
        segment_count=len(intermediates),
        used_subtitles=used_subs,
    )


def cleanup_intermediates(intermediate_dir: Path) -> None:
    """Remove the per-draft scratch directory after a successful render."""
    if intermediate_dir.is_dir():
        shutil.rmtree(intermediate_dir, ignore_errors=True)


__all__ = [
    "ASPECT_DIMENSIONS",
    "AUDIO_BITRATE",
    "AUDIO_CODEC",
    "CONCAT_TIMEOUT_S",
    "FFmpegMissingError",
    "PER_SEGMENT_TIMEOUT_S",
    "RenderResult",
    "SUBTITLE_BURN_TIMEOUT_S",
    "SUBTITLE_FORCE_STYLE",
    "subtitle_force_style",
    "VIDEO_CODEC",
    "VIDEO_CRF",
    "VIDEO_FPS",
    "VIDEO_PIX_FMT",
    "VIDEO_PRESET",
    "VideoRenderError",
    "VideoRenderTimeoutError",
    "aspect_filter",
    "burn_subtitles",
    "cleanup_intermediates",
    "concat_segments",
    "cut_segments",
    "render",
]
