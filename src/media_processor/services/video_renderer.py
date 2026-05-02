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
from typing import Any

from media_processor.services import auto_reframe
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
# subtitles filter. Retained even though burn_subtitles now uses drawtext
# in case external callers / tests still import the constant.
SUBTITLE_FORCE_STYLE: str = subtitle_force_style("9:16")

# drawtext-based subtitle burn-in (replaces libass subtitles= filter so
# Fontsize is pixel-accurate against the actual render canvas instead of
# relying on the SRT→ASS conversion picking a sane PlayRes).
SUBTITLE_FONT_PATH: str = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
SUBTITLE_FONT_SIZE: int = 42
SUBTITLE_BORDER_W: int = 2
SUBTITLE_BOTTOM_OFFSET_PX: int = 80  # y=h-N from frame bottom

# v0.18 — secondary-language subtitle layer (dual-language rendering).
# Same font file (Noto CJK ships Latin glyphs with Roman fallback) and
# stroke; smaller font and stacked above the primary cue with a
# vertical gap so the two never overlap. Sized so a 2-line zh-Hant
# primary at 42 px + 2 px border + 28 px secondary fits inside the
# 9:16 safe area on a 1920 h canvas.
SUBTITLE_SECONDARY_FONT_SIZE: int = 28
SUBTITLE_SECONDARY_BORDER_W: int = 2
# Vertical gap between the top of the primary cue's bounding box and
# the bottom of the secondary cue. Computed at render time using the
# primary's text_h variable so multi-line primary cues still leave the
# secondary visible above them.
SUBTITLE_SECONDARY_GAP_PX: int = 12

# Timeouts. Per-call covers a single ffmpeg invocation; the worker job
# layers its own outer cap on the whole render.
PER_SEGMENT_TIMEOUT_S: float = 300.0
CONCAT_TIMEOUT_S: float = 300.0
SUBTITLE_BURN_TIMEOUT_S: float = 600.0
STABILIZE_TIMEOUT_S: float = 600.0  # two-pass vidstab is slow

# v0.14.3 — digital stabilization (vidstabdetect + vidstabtransform).
# Two-pass: first pass writes a transforms file describing the shake,
# second pass applies the inverse transform. Defaults are tuned for
# handheld phone footage; raising shakiness or smoothing too high blurs
# the image instead of stabilising it.
STABILIZE_SHAKINESS: int = 8  # 1-10, how shaky the input is
STABILIZE_ACCURACY: int = 9  # 1-15, more accurate = slower
STABILIZE_STEPSIZE: int = 6  # search-step size in px
STABILIZE_SMOOTHING: int = 10  # half-window of frames to smooth over
STABILIZE_ZOOM: int = 0  # extra zoom % during transform; 0 = letterbox


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


# Phase 8.1 — emotion-driven zoompan. Excited / surprised cuts get a slow
# zoom-in (1.0 → ZOOMPAN_END_ZOOM over the cut's duration) so the camera
# tracks the energy of the moment; serious / neutral cuts stay locked
# off. ZOOMPAN_FPS matches VIDEO_FPS so the zoompan filter doesn't
# resample mid-clip.
ZOOMPAN_EMOTIONS: frozenset[str] = frozenset({"happy", "surprised"})
ZOOMPAN_END_ZOOM: float = 1.15
ZOOMPAN_FPS: int = VIDEO_FPS

# Camera-motion classes that already carry visual energy on their own —
# combined with a dynamic emotion they make zoompan feel earned. Mirror
# of ``edit_planner.DYNAMIC_MOTIONS``; duplicated here so the renderer
# stays a pure ffmpeg wrapper without a planner import dep.
ZOOMPAN_DYNAMIC_MOTIONS: frozenset[str] = frozenset({"pan", "tilt", "handheld"})


def _should_zoompan(cut: CutPlanSegment) -> bool:
    """Decide whether ``cut`` should get the slow zoom-in chain.

    Three conditions must all hold:
      * Dominant emotion is one we've decided is energetic enough to
        motivate a zoom (``happy`` / ``surprised``).
      * EITHER the source camera was moving (pan / tilt / handheld) OR
        a face was actually visible inside the chosen span.

    Without the second clause we'd zoom on a static, faceless clip
    (e.g. a product shot whose surrounding asset happened to score as
    ``happy`` from a face elsewhere) and the result reads as a frozen
    photo with a slow Ken Burns layered on top — exactly the "looks
    frozen" failure mode users reported on M8.1.
    """
    if getattr(cut, "dominant_emotion", "neutral") not in ZOOMPAN_EMOTIONS:
        return False
    motion = getattr(cut, "dominant_motion", "static")
    has_face = bool(getattr(cut, "has_face", False))
    return motion in ZOOMPAN_DYNAMIC_MOTIONS or has_face


def _zoompan_filter(target_aspect: str, duration_s: float) -> str:
    """Build a ``zoompan`` filter chain that smoothly zooms 1.0 → 1.15.

    Critical: ``d=1`` so each *input* frame produces ONE output frame —
    that keeps the underlying video playing while the zoom progresses.
    The previous implementation set ``d=total_frames``, which is the
    Ken-Burns "still photo zoom" mode: ffmpeg holds the first input
    frame for total_frames output frames, freezing the clip for its
    entire duration. That mismatch is what users reported as
    "zoompan looks frozen" on M8.1.

    The per-frame increment is sized so that across ``total_frames``
    output frames the zoom lands exactly at ``ZOOMPAN_END_ZOOM``,
    regardless of cut length. ``s=`` matches ASPECT_DIMENSIONS so the
    surrounding aspect chain doesn't have to crop again.
    """
    width, height = ASPECT_DIMENSIONS[target_aspect]
    duration_s = max(0.001, duration_s)
    total_frames = max(1, int(round(duration_s * ZOOMPAN_FPS)))
    # Per-frame zoom increment so we land at ZOOMPAN_END_ZOOM after
    # total_frames output frames; clamped with min(...) so rounding
    # never overshoots even with float drift.
    increment = (ZOOMPAN_END_ZOOM - 1.0) / float(total_frames)
    return (
        f"zoompan="
        f"z='min(zoom+{increment:.6f},{ZOOMPAN_END_ZOOM})'"
        f":d=1"
        f":x='iw/2-(iw/zoom)/2'"
        f":y='ih/2-(ih/zoom)/2'"
        f":s={width}x{height}"
        f":fps={ZOOMPAN_FPS}"
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
    *,
    tracking: dict[str, Any] | None = None,
    sendcmd_dir: Path | None = None,
    tracking_object_index: int | None = None,
    custom_roi: dict[str, Any] | None = None,
) -> None:
    """Cut + scale-and-crop one segment to a uniform intermediate mp4.

    Phase 8.1: when the cut's ``dominant_emotion`` is in
    ``ZOOMPAN_EMOTIONS`` we tack a ``zoompan`` filter onto the chain so
    the segment renders with a slow 1.00 → 1.15 zoom-in across its
    duration. Other emotions (or unknown) keep the static aspect crop.

    v0.16: when ``tracking`` (per-asset YOLO bbox dict from
    ``Asset.tracking_json``) is supplied AND covers this cut's window,
    the static aspect filter is replaced by the
    ``sendcmd → crop@reframe → scale`` chain from
    :mod:`auto_reframe` so the subject stays centered across the cut.
    Falls back to the static crop when tracking has no overlapping
    frames or the source already matches the target aspect.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    start_s = cut.asset_start_ms / 1000.0
    duration_s = max(0.001, (cut.asset_end_ms - cut.asset_start_ms) / 1000.0)

    vf_chain = aspect_filter(target_aspect)
    # v0.17 — auto-reframe input picks between three sources:
    #   custom_roi  → user-drawn ROI tracked through CSRT
    #   tracking + tracking_object_index  → user-picked YOLO track
    #   tracking only → dominant YOLO track (historic default)
    crop_path = None
    if sendcmd_dir is not None:
        if custom_roi:
            crop_path = auto_reframe.compute_crop_path_from_custom_roi(
                custom_roi,
                target_aspect=target_aspect,
                asset_start_ms=cut.asset_start_ms,
                asset_end_ms=cut.asset_end_ms,
            )
        elif tracking:
            crop_path = auto_reframe.compute_crop_path(
                tracking,
                target_aspect=target_aspect,
                asset_start_ms=cut.asset_start_ms,
                asset_end_ms=cut.asset_end_ms,
                object_index=tracking_object_index,
            )
        if crop_path is not None:
            sendcmd_path = sendcmd_dir / f"reframe_seg_{cut.order:04d}.txt"
            auto_reframe.write_sendcmd_file(crop_path, sendcmd_path)
            target_w, target_h = ASPECT_DIMENSIONS[target_aspect]
            vf_chain = auto_reframe.build_filter_chain(
                crop_path, sendcmd_path, target_w, target_h
            )

    if _should_zoompan(cut):
        # zoompan operates on its own canvas, so we run it AFTER the
        # aspect crop so the zoom centre is the cropped frame's centre
        # rather than the original asset's.
        vf_chain = f"{vf_chain},{_zoompan_filter(target_aspect, duration_s)}"
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
        vf_chain,
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
    tracking_by_asset: dict[int, dict[str, Any]] | None = None,
    tracking_target_by_asset: dict[int, int | None] | None = None,
    custom_roi_by_asset: dict[int, dict[str, Any]] | None = None,
) -> list[Path]:
    """Cut every segment in the plan; return the intermediate paths in order.

    ``tracking_by_asset`` (when supplied) maps ``asset_id`` to its
    ``Asset.tracking_json`` dict; segments backed by an asset present in
    that map get the auto-reframe dynamic crop chain. A None value
    or a missing key means the segment falls back to the static
    aspect crop. The renderer caller decides whether the user opted
    in to auto-reframe; this layer only reacts to the dict it gets.

    ``tracking_target_by_asset`` (v0.17) maps ``asset_id`` →
    ``object_index`` for the chosen track inside ``tracking``. Special
    sentinels: ``-1`` = use ``custom_roi_by_asset[asset_id]``;
    ``-2``/``-3`` = no auto-reframe (caller is expected to omit
    ``tracking_by_asset`` for those, but we double-check here too).
    """
    _require_ffmpeg()
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    sendcmd_dir = intermediate_dir / "reframe"
    has_any_reframe = bool(tracking_by_asset) or bool(custom_roi_by_asset)
    if has_any_reframe:
        sendcmd_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []
    total = len(plan.segments)
    for cut in plan.segments:
        src = asset_paths.get(cut.asset_id)
        if src is None or not Path(src).is_file():
            raise VideoRenderError(f"segment {cut.order}: asset {cut.asset_id} source missing")
        out = intermediate_dir / f"seg_{cut.order:04d}.mp4"
        track = (tracking_by_asset or {}).get(cut.asset_id)
        target_idx = (tracking_target_by_asset or {}).get(cut.asset_id)
        custom_roi = (custom_roi_by_asset or {}).get(cut.asset_id)
        # Sentinels disable auto-reframe entirely; defensively clear
        # the inputs so the chain falls back to the static aspect crop.
        if target_idx in (-2, -3):
            track = None
            custom_roi = None
        _cut_segment(
            Path(src),
            cut,
            out,
            target_aspect,
            tracking=track,
            sendcmd_dir=sendcmd_dir if has_any_reframe else None,
            tracking_object_index=target_idx if (target_idx is not None and target_idx >= 0) else None,
            custom_roi=custom_roi if target_idx == -1 else None,
        )
        out_paths.append(out)
        if on_progress is not None:
            on_progress(cut.order + 1, total)
    return out_paths


# ---------- stage 1.5: digital stabilization (optional) ----------


def _stabilize_segment(src: Path, dst: Path, scratch_dir: Path) -> None:
    """Two-pass vidstab on ``src`` writing to ``dst``.

    Pass 1 (``vidstabdetect``) walks the clip and writes a per-frame
    transforms file describing the shake. Pass 2 (``vidstabtransform``)
    applies the inverse transform plus a light unsharp mask to recover
    the softness vidstab leaves behind. Both passes are sync ffmpeg
    invocations bounded by ``STABILIZE_TIMEOUT_S``.

    The transforms file lives next to the segment so a re-run can
    inspect / reuse it; ``cleanup_intermediates`` later wipes the
    whole scratch dir.
    """
    src = Path(src)
    dst = Path(dst)
    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    transforms_path = scratch_dir / f"{src.stem}.trf"

    detect_filter = (
        f"vidstabdetect=stepsize={STABILIZE_STEPSIZE}"
        f":shakiness={STABILIZE_SHAKINESS}"
        f":accuracy={STABILIZE_ACCURACY}"
        f":result={transforms_path.as_posix()}"
    )
    detect_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vf",
        detect_filter,
        "-f",
        "null",
        "-",
    ]
    _run(detect_cmd, timeout_s=STABILIZE_TIMEOUT_S, stage=f"stabilize-detect({src.name})")

    transform_filter = (
        f"vidstabtransform=input={transforms_path.as_posix()}"
        f":zoom={STABILIZE_ZOOM}"
        f":smoothing={STABILIZE_SMOOTHING}"
        ",unsharp=5:5:0.8:3:3:0.4"
    )
    transform_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vf",
        transform_filter,
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
        str(dst),
    ]
    _run(transform_cmd, timeout_s=STABILIZE_TIMEOUT_S, stage=f"stabilize-apply({src.name})")


def stabilize_segments(
    intermediate_paths: list[Path],
    intermediate_dir: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[Path]:
    """Run two-pass vidstab over each per-segment intermediate.

    Replaces each ``seg_NNNN.mp4`` in-place by writing a stabilised
    version to ``seg_NNNN.stab.mp4`` and returning the new path list.
    The originals stay on disk until ``cleanup_intermediates`` runs so
    a stabilize bug doesn't lose the un-stabilised render.
    """
    _require_ffmpeg()
    out: list[Path] = []
    total = len(intermediate_paths)
    for i, src in enumerate(intermediate_paths):
        stab_dst = intermediate_dir / f"{src.stem}.stab.mp4"
        _stabilize_segment(src, stab_dst, intermediate_dir)
        out.append(stab_dst)
        if on_progress is not None:
            on_progress(i + 1, total)
    return out


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


# Transition knobs — kept locally rather than imported from edit_planner
# so video_renderer stays usable as a pure ffmpeg wrapper. The whitelist
# is the ffmpeg xfade values we promise to support; anything else from a
# stored plan is coerced to the safe default.
TRANSITION_DURATION_S: float = 0.5
# Whitelist of ffmpeg xfade values we ship. v0.14.3 dropped ``fade`` and
# ``dissolve`` after operator feedback that every reel looked the same;
# only the assertive variants survive (wipe / slide / circlecrop). Any
# legacy value from a stored plan is coerced to TRANSITION_DEFAULT
# inside ``_safe_transition`` so older serialised plans still render.
VALID_TRANSITIONS: frozenset[str] = frozenset(
    {"wipeleft", "slideright", "circlecrop"}
)
TRANSITION_DEFAULT: str = "wipeleft"


def _safe_transition(name: str) -> str:
    """Coerce any plan-provided transition name to a safe whitelisted one."""
    return name if name in VALID_TRANSITIONS else TRANSITION_DEFAULT


def _build_xfade_filter(
    durations_ms: list[int],
    transitions: list[str],
) -> tuple[str, str]:
    """Build (video_chain, audio_chain) for N inputs → [vout]/[aout].

    Video uses xfade with cumulative offsets so adjacent cuts overlap by
    TRANSITION_DURATION_S. Audio uses acrossfade with the same duration —
    it auto-aligns to the end of each stream so no offset arithmetic is
    needed there. Caller guarantees ``len(durations_ms) >= 2`` and
    ``len(transitions) >= len(durations_ms) - 1``.
    """
    n = len(durations_ms)
    td = TRANSITION_DURATION_S

    v_parts: list[str] = []
    cumulative_s = durations_ms[0] / 1000.0
    prev = "[0:v]"
    for i in range(1, n):
        offset = max(0.0, cumulative_s - td)
        out_label = "[vout]" if i == n - 1 else f"[v{i}]"
        t = _safe_transition(transitions[i - 1])
        v_parts.append(
            f"{prev}[{i}:v]xfade=transition={t}:duration={td}:offset={offset:.3f}{out_label}"
        )
        cumulative_s += durations_ms[i] / 1000.0 - td
        prev = out_label

    a_parts: list[str] = []
    prev = "[0:a]"
    for i in range(1, n):
        out_label = "[aout]" if i == n - 1 else f"[a{i}]"
        a_parts.append(f"{prev}[{i}:a]acrossfade=d={td}:c1=tri:c2=tri{out_label}")
        prev = out_label

    return ";".join(v_parts), ";".join(a_parts)


def concat_segments(
    intermediate_paths: list[Path],
    output_path: Path,
    list_path: Path,
    *,
    durations_ms: list[int] | None = None,
    transitions: list[str] | None = None,
) -> None:
    """Concat intermediates into a single mp4.

    Two paths:
      - **Plain mux** (default, when ``durations_ms`` / ``transitions`` are
        omitted or there's only one segment) — ffmpeg's concat demuxer
        with ``-c copy``. Fast, no re-encode, what M5 used pre-6.3.
      - **xfade chain** (when both lists provided AND len ≥ 2) — feeds
        every intermediate as a separate input and chains
        ``xfade``/``acrossfade`` between them so adjacent cuts overlap by
        ``TRANSITION_DURATION_S``. Re-encodes (xfade can't operate on
        compressed streams).

    ``list_path`` is still written in both modes so the demuxer fallback
    stays a one-line config change away.
    """
    _require_ffmpeg()
    if not intermediate_paths:
        raise VideoRenderError("concat: no intermediate segments to join")
    _write_concat_list(intermediate_paths, list_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    use_xfade = (
        durations_ms is not None
        and transitions is not None
        and len(intermediate_paths) >= 2
        and len(durations_ms) == len(intermediate_paths)
        and len(transitions) >= len(intermediate_paths) - 1
    )

    if use_xfade:
        v_chain, a_chain = _build_xfade_filter(durations_ms, transitions)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        for p in intermediate_paths:
            cmd += ["-i", str(p)]
        cmd += [
            "-filter_complex",
            f"{v_chain};{a_chain}",
            "-map",
            "[vout]",
            "-map",
            "[aout]",
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
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        _run(cmd, timeout_s=CONCAT_TIMEOUT_S, stage="concat")
        return

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


def _srt_timestamp_to_seconds(ts: str) -> float:
    """``HH:MM:SS,mmm`` → float seconds. SRT uses ',' for ms separator."""
    h, m, rest = ts.strip().split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def _parse_srt_cues(srt_text: str) -> list[tuple[float, float, str]]:
    """Return ``[(start_s, end_s, text), …]``. Tolerant — bad blocks are skipped.

    The text retains internal newlines so drawtext can render multi-line
    cues by translating ``\\n`` → backslash-n in :func:`_drawtext_escape`.
    """
    cues: list[tuple[float, float, str]] = []
    # Split on blank line; \r\n vs \n both common in SRT in the wild.
    for raw_block in srt_text.replace("\r\n", "\n").strip().split("\n\n"):
        lines = raw_block.split("\n")
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            start_str, end_str = lines[1].split("-->")
            start_s = _srt_timestamp_to_seconds(start_str)
            end_s = _srt_timestamp_to_seconds(end_str)
        except (ValueError, IndexError):
            continue
        text = "\n".join(lines[2:]).strip()
        if not text or end_s <= start_s:
            continue
        cues.append((start_s, end_s, text))
    return cues


def _drawtext_escape(text: str) -> str:
    """Escape ``text`` so it can sit inside ``text='…'`` of a drawtext filter.

    Order matters: backslash first (otherwise we double-escape later
    substitutions). Real newlines in input become ``\\n`` so drawtext
    renders them as line breaks (under default expansion=normal).
    """
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    text = text.replace("\n", "\\n")
    return text


def _build_drawtext_chain(
    cues: list[tuple[float, float, str]],
    secondary_cues: list[tuple[float, float, str]] | None = None,
) -> str:
    """Build a comma-chained drawtext filtergraph for primary (+ optional secondary) cues.

    Each filter is gated by ``enable=between(t,start,end)`` so only the
    active cue draws on any given frame. Primary cue position:
    ``x=(w-text_w)/2`` (centered), ``y=h-80`` (anchored 80px from
    bottom). Secondary cue (when supplied) sits ABOVE the primary at
    ``y = h - SUBTITLE_BOTTOM_OFFSET_PX - SUBTITLE_FONT_SIZE * MAX_LINES
            - SUBTITLE_SECONDARY_GAP_PX - text_h``
    so a two-line primary cue still leaves the secondary visible above
    it. Filter ordering: primary first, then secondary, so the
    secondary is the last layer drawn (on top of any visual primaries
    in the rare overlap window where their enable ranges align).
    """
    parts: list[str] = []
    for start_s, end_s, text in cues:
        escaped = _drawtext_escape(text)
        parts.append(
            f"drawtext=fontfile={SUBTITLE_FONT_PATH}"
            f":fontsize={SUBTITLE_FONT_SIZE}"
            f":fontcolor=white"
            f":borderw={SUBTITLE_BORDER_W}"
            f":bordercolor=black"
            f":x=(w-text_w)/2"
            f":y=h-{SUBTITLE_BOTTOM_OFFSET_PX}"
            f":text='{escaped}'"
            f":enable=between(t\\,{start_s:.3f}\\,{end_s:.3f})"
        )

    # Compute the secondary baseline once: the primary cue uses up to
    # MAX_LINES * SUBTITLE_FONT_SIZE px of vertical real estate above
    # h - SUBTITLE_BOTTOM_OFFSET_PX. Secondary text_h is variable
    # (drawtext expression), so subtract it dynamically.
    primary_height_px = SUBTITLE_FONT_SIZE * 2  # MAX_LINES = 2 in subtitles.py
    secondary_baseline_px = (
        SUBTITLE_BOTTOM_OFFSET_PX + primary_height_px + SUBTITLE_SECONDARY_GAP_PX
    )
    if secondary_cues:
        for start_s, end_s, text in secondary_cues:
            escaped = _drawtext_escape(text)
            parts.append(
                f"drawtext=fontfile={SUBTITLE_FONT_PATH}"
                f":fontsize={SUBTITLE_SECONDARY_FONT_SIZE}"
                f":fontcolor=white"
                f":borderw={SUBTITLE_SECONDARY_BORDER_W}"
                f":bordercolor=black"
                f":x=(w-text_w)/2"
                f":y=h-{secondary_baseline_px}-text_h"
                f":text='{escaped}'"
                f":enable=between(t\\,{start_s:.3f}\\,{end_s:.3f})"
            )
    return ",".join(parts)


def burn_subtitles(
    concat_path: Path,
    srt_path: Path | None,
    output_path: Path,
    target_aspect: str = "9:16",
    *,
    secondary_srt_path: Path | None = None,
) -> None:
    """Re-encode ``concat_path`` with subtitles burned in via drawtext.

    Replaces the previous libass subtitles= filter chain. drawtext's
    ``fontsize`` is in actual pixel units of the render canvas, so we no
    longer depend on the SRT→ASS PlayRes conversion picking a sane scale.
    Each SRT cue becomes one drawtext filter gated by ``enable=between``;
    a render with no cues still re-encodes (stays consistent with the
    pre-drawtext behaviour of always producing a fresh mp4 here).

    ``target_aspect`` is accepted for signature compatibility — drawtext
    sizing is uniform across canvases now.

    v0.18 — when ``secondary_srt_path`` is supplied and present on disk
    we layer a second drawtext chain (smaller font, positioned above
    the primary cue) so the rendered mp4 carries dual-language
    subtitles. Missing or empty secondary file = primary-only burn.
    """
    _require_ffmpeg()
    if target_aspect not in ASPECT_DIMENSIONS:
        raise VideoRenderError(f"unsupported target aspect ratio: {target_aspect!r}")
    if not concat_path.is_file() and not _is_fake():
        raise VideoRenderError(f"burn: concat output missing at {concat_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cues: list[tuple[float, float, str]] = []
    if srt_path is not None and srt_path.is_file():
        try:
            cues = _parse_srt_cues(srt_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise VideoRenderError(f"burn: cannot read SRT at {srt_path}: {exc}") from exc

    secondary_cues: list[tuple[float, float, str]] = []
    if secondary_srt_path is not None and secondary_srt_path.is_file():
        try:
            secondary_cues = _parse_srt_cues(
                secondary_srt_path.read_text(encoding="utf-8")
            )
        except OSError as exc:
            # Non-fatal: primary still burns. Log and skip the secondary
            # layer rather than failing the whole subtitles stage.
            logger.warning(
                "burn: cannot read secondary SRT at %s: %s — skipping",
                secondary_srt_path,
                exc,
            )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(concat_path),
    ]
    if cues or secondary_cues:
        cmd += ["-vf", _build_drawtext_chain(cues, secondary_cues or None)]
    cmd += [
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
    secondary_srt_path: Path | None = None,
    stabilize: bool = True,
    transitions_enabled: bool = True,
    tracking_by_asset: dict[int, dict[str, Any]] | None = None,
    tracking_target_by_asset: dict[int, int | None] | None = None,
    custom_roi_by_asset: dict[int, dict[str, Any]] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> RenderResult:
    """Run the render stages end-to-end.

    ``on_progress(stage, done, total)`` fires after each stage advance —
    the worker uses it to update ``Draft.progress_steps_json``. ``stage``
    is one of ``"cut" | "stabilize" | "concat" | "subtitles"``.

    ``stabilize`` (default ``True``) enables the v0.14.3 two-pass
    vidstab pipeline between cut and concat. Each per-segment
    intermediate is replaced with a stabilised version. Roughly doubles
    render time for the per-cut work but removes handheld shake.

    ``transitions_enabled`` (default ``True``) enables the xfade chain
    between adjacent cuts. When False the concat stage falls back to
    the plain demuxer mux (hard cuts, no overlap), matching the old
    pre-M6.3 behaviour. Useful for tight news-style edits.

    ``tracking_by_asset`` (default ``None``) opts the cut stage into
    the v0.16 auto-reframe dynamic crop. When supplied, every segment
    whose source asset is keyed in the dict gets a Kalman-smoothed
    sendcmd-driven crop window; segments without tracking data fall
    back to the static centered aspect crop. When None, every segment
    uses the static crop (M6 behaviour).
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
        tracking_by_asset=tracking_by_asset,
        tracking_target_by_asset=tracking_target_by_asset,
        custom_roi_by_asset=custom_roi_by_asset,
    )

    # Stage 1.5 — optional digital stabilization. Replaces each
    # intermediate with a stabilised version before concat. The two-pass
    # vidstab is the slow part of the pipeline so we surface it as its
    # own progress bucket.
    if stabilize:
        def _stab_progress(done: int, total: int) -> None:
            if on_progress is not None:
                on_progress("stabilize", done, total)

        intermediates = stabilize_segments(
            intermediates,
            intermediate_dir,
            on_progress=_stab_progress,
        )

    # Stage 2 — concat into the final output path. If we're going to burn
    # subtitles we still concat first so a subtitle failure leaves a
    # playable preview behind. Pass per-cut durations + transitions so the
    # concat stage uses xfade chains instead of plain mux when we have
    # ≥2 cuts; a single-cut plan still goes through the demuxer copy
    # path automatically.
    list_path = intermediate_dir / "concat.txt"
    # Burn pass needs an intermediate concat output if EITHER subtitle
    # layer is going to be added. Without that, the burn step would try
    # to read and write the same path. v0.18 widened this from
    # primary-only to (primary OR secondary).
    will_burn = srt_path is not None or secondary_srt_path is not None
    concat_path = intermediate_dir / "concat.mp4" if will_burn else output_path
    # Hand the xfade lists to ``concat_segments`` only when the user
    # actually asked for transitions; passing ``transitions=None`` makes
    # the helper fall through to the plain concat-demuxer ``-c copy``
    # path, which is hard-cut + no re-encode.
    if transitions_enabled and len(plan.segments) > 1:
        durations_ms: list[int] | None = [
            s.asset_end_ms - s.asset_start_ms for s in plan.segments
        ]
        transitions: list[str] | None = [
            s.transition_to_next for s in plan.segments[:-1]
        ]
    else:
        durations_ms = None
        transitions = None
    concat_segments(
        intermediates,
        concat_path,
        list_path,
        durations_ms=durations_ms,
        transitions=transitions,
    )
    if on_progress is not None:
        on_progress("concat", 1, 1)

    used_subs = False
    has_primary_srt = (
        srt_path is not None and srt_path.is_file() and srt_path.stat().st_size > 0
    )
    has_secondary_srt = (
        secondary_srt_path is not None
        and secondary_srt_path.is_file()
        and secondary_srt_path.stat().st_size > 0
    )
    if has_primary_srt or has_secondary_srt:
        # When only the secondary track has cues, pass srt_path=None so
        # ``burn_subtitles`` parses an empty primary cue list and emits
        # just the secondary drawtext layer.
        burn_subtitles(
            concat_path,
            srt_path if has_primary_srt else None,
            output_path,
            target_aspect,
            secondary_srt_path=secondary_srt_path if has_secondary_srt else None,
        )
        used_subs = True
    elif will_burn:
        # Caller asked for subtitles but neither SRT exists on disk
        # (transcript-less project, or translation never ran). Fall
        # back to copying the concat output to the final path so the
        # mp4 is still delivered.
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
    "STABILIZE_TIMEOUT_S",
    "SUBTITLE_BURN_TIMEOUT_S",
    "SUBTITLE_FORCE_STYLE",
    "subtitle_force_style",
    "TRANSITION_DEFAULT",
    "TRANSITION_DURATION_S",
    "VALID_TRANSITIONS",
    "VIDEO_CODEC",
    "VIDEO_CRF",
    "VIDEO_FPS",
    "VIDEO_PIX_FMT",
    "VIDEO_PRESET",
    "VideoRenderError",
    "VideoRenderTimeoutError",
    "ZOOMPAN_DYNAMIC_MOTIONS",
    "ZOOMPAN_EMOTIONS",
    "ZOOMPAN_END_ZOOM",
    "aspect_filter",
    "burn_subtitles",
    "cleanup_intermediates",
    "concat_segments",
    "cut_segments",
    "render",
    "stabilize_segments",
]
