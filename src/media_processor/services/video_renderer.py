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
# variant is the IG / TikTok native upload size; 1920×1080 for the
# landscape variant matches YouTube / web-embed deliverables. Fixed
# canvas dims keep the per-segment scale + crop deterministic.
#
# v0.29.0 — dropped 4:5 (1080×1350) and 1:1 (1080×1080); added 16:9
# (1920×1080). Operators stopped shipping IG-feed posts and asked
# for a horizontal landscape variant. Migration 0026 rewrites legacy
# 4:5/1:1 projects to 9:16 so a stale Project row never feeds a key
# the renderer no longer knows.
ASPECT_DIMENSIONS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
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
    the frame on portrait; the landscape variant gets a slightly larger
    Fontsize and a smaller bottom margin because the canvas is shorter.
    Returns a comma-separated string suitable for ffmpeg's
    ``force_style=`` value.

    v0.29.0 — replaced 4:5 / 1:1 branches with a single 16:9 branch.
    The active subtitle pipeline is drawtext (libass force_style is
    legacy plumbing kept for back-compat tests), so the practical
    effect of these numbers is small — drawtext sizing comes from
    SUBTITLE_*_CHOICES.
    """
    width, _ = ASPECT_DIMENSIONS[target_aspect]
    if target_aspect == "9:16":
        font_size = 28
        margin_v = 180
    else:  # "16:9"
        font_size = 32
        margin_v = 60
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

# v0.18 — user-customisable subtitle style. The frontend picks one entry
# from each map; the renderer hands the chosen pixel value into the
# drawtext filter. Defaults are baked here so a project that hasn't
# been touched (or a stale CutPlan re-render) keeps the historic look.
#
# Font keys are stable strings stored on Project.subtitle_font; the path
# is the file the worker container ships (fonts-noto-cjk apt package
# under docker/worker.Dockerfile). New entries here MUST also exist on
# disk inside the worker image or drawtext silently falls back to its
# own default and CJK glyphs render as tofu.
SUBTITLE_FONT_CHOICES: dict[str, str] = {
    "noto_sans_tc": "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "noto_sans_tc_bold": "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "noto_serif_tc": "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
}
SUBTITLE_SIZE_CHOICES: dict[str, int] = {
    "small": 32,
    "medium": 42,
    "large": 56,
}
SUBTITLE_OUTLINE_WIDTH_CHOICES: dict[str, int] = {
    "none": 0,
    "thin": 2,
    "thick": 5,
}
# Position is computed at filter-build time off the canvas height so the
# y-expression stays correct across 9:16 / 16:9 (v0.29.0). ``middle`` centres
# vertically; ``top`` / ``bottom`` anchor with a small inset so the text
# doesn't kiss the frame edge.
SUBTITLE_POSITION_CHOICES: tuple[str, ...] = ("top", "middle", "bottom")
SUBTITLE_TOP_OFFSET_PX: int = 80

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


@dataclass(frozen=True)
class SubtitleStyle:
    """Renderer-level subtitle style. Built from Project.subtitle_* by
    the orchestrator. Defaults match the pre-v0.18 hard-coded look so
    callers that don't know about styling get the historic burn-in.
    Resolution to font path / pixel sizes happens at filter-build time
    in :func:`_build_drawtext_chain` so an unknown key falls back here
    rather than failing the whole render.
    """

    font: str = "noto_sans_tc"
    color: str = "#ffffff"
    outline_color: str = "#000000"
    position: str = "bottom"
    size: str = "medium"
    outline_width: str = "thin"


def _hex_to_drawtext_color(hex_color: str) -> str:
    """Convert ``#rrggbb`` (or shorthand ``#rgb``) to drawtext's
    ``0xRRGGBB`` form. Falls back to white on a malformed input rather
    than blowing up — drawtext rejects unknown colour syntax with a
    fatal error which would fail the whole render."""
    s = hex_color.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        return "0xFFFFFF"
    return f"0x{s.upper()}"


# Timeouts. Per-call covers a single ffmpeg invocation; the worker job
# layers its own outer cap on the whole render.
PER_SEGMENT_TIMEOUT_S: float = 300.0
CONCAT_TIMEOUT_S: float = 300.0
SUBTITLE_BURN_TIMEOUT_S: float = 600.0
STABILIZE_TIMEOUT_S: float = 600.0  # two-pass vidstab is slow
WATERMARK_TIMEOUT_S: float = 600.0

# v0.18 — watermark / brand-logo overlay knobs. Position is one of nine
# anchor points in a 3x3 grid; scale is the logo width as a fraction of
# the rendered canvas width; opacity is the alpha multiplier applied via
# colorchannelmixer. Bounds match ``schemas.WatermarkSettingsPatch`` —
# we re-clamp here so a stale row (or a direct call from a test) can't
# blow up ffmpeg with a degenerate size.
WATERMARK_POSITIONS: frozenset[str] = frozenset(
    {
        "top-left",
        "top-center",
        "top-right",
        "middle-left",
        "middle-center",
        "middle-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    }
)
WATERMARK_DEFAULT_POSITION: str = "bottom-right"
WATERMARK_SCALE_MIN: float = 0.02
WATERMARK_SCALE_MAX: float = 0.5
WATERMARK_OPACITY_MIN: float = 0.0
WATERMARK_OPACITY_MAX: float = 1.0
WATERMARK_MARGIN_RATIO: float = 0.02  # 2% of canvas width on each edge
WATERMARK_MARGIN_MIN_PX: int = 12

# v0.14.3 — digital stabilization (vidstabdetect + vidstabtransform).
# Two-pass: first pass writes a transforms file describing the shake,
# second pass applies the inverse transform. v0.30.18 rolls back the
# over-aggressive 0.30.16 preset: stacking high smoothing / adaptive zoom
# after Smart Camera could create a visible correction shove around cut
# interiors. Keep the previously stable handheld preset instead.
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


def aspect_filter(
    target_aspect: str,
    *,
    crop_region: tuple[float, float] | None = None,
) -> str:
    """Return the ``scale=…,crop=…,setsar=1`` filter chain for the target.

    ``crop_region`` (v0.29.0) is the optional ``(x_norm, y_norm)`` static-
    crop anchor used when source orientation differs from target
    orientation (e.g. 9:16 source → 16:9 target). Each value is 0..1 and
    represents the fraction of the source where the crop window's
    top-left anchor lands; (0.5, 0.5) is centre, which is exactly what
    the default ffmpeg ``crop=W:H`` already does, so we omit explicit
    x/y in that case.

    For non-centre anchors we expand the chain to
    ``crop=W:H:x_expr:y_expr`` where the expressions reference ffmpeg's
    ``in_w``/``in_h`` variables (the post-scale dimensions). The
    expressions also clamp to ``[0, in_w-W]`` / ``[0, in_h-H]`` so an
    operator who picked the extreme edge gets a window that snaps
    cleanly inside the source instead of black bars (or worse, a
    negative-coordinate ffmpeg error).
    """
    if target_aspect not in ASPECT_DIMENSIONS:
        raise VideoRenderError(f"unsupported target aspect ratio: {target_aspect!r}")
    width, height = ASPECT_DIMENSIONS[target_aspect]
    crop = f"crop={width}:{height}"
    if crop_region is not None:
        x_norm, y_norm = crop_region
        # Clamp inputs defensively; the API layer also clamps but
        # this keeps the renderer self-contained.
        x_norm = max(0.0, min(1.0, float(x_norm)))
        y_norm = max(0.0, min(1.0, float(y_norm)))
        # Only emit the longer crop expression when the anchor is
        # actually off-centre; centre is a tight equality check
        # because that's the only value where the default crop
        # already does the right thing.
        if not (abs(x_norm - 0.5) < 1e-6 and abs(y_norm - 0.5) < 1e-6):
            x_expr = f"max(0\\,min(in_w-{width}\\,{x_norm:.4f}*(in_w-{width})))"
            y_expr = f"max(0\\,min(in_h-{height}\\,{y_norm:.4f}*(in_h-{height})))"
            crop = f"crop={width}:{height}:{x_expr}:{y_expr}"
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,{crop},setsar=1"


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


# v0.30.0 — opt-in AI Smart Camera filter. When the planner stored a
# directive on ``CutPlanSegment.smart_camera_json`` AND no higher-
# priority crop chain claimed the cut (vidstab on / auto-reframe on),
# the renderer drives a per-cut ``crop=W:H:x:y`` expression that
# interpolates between ``from_rect`` and ``to_rect`` across the cut's
# duration. The expression is pure ffmpeg ``-vf`` syntax — no
# sendcmd file needed for zoom_in / zoom_out, and pan re-uses the
# same expression form (just different from/to rectangles).
SMART_CAMERA_KINDS: frozenset[str] = frozenset({"zoom_in", "zoom_out", "pan"})


def _smart_camera_filter(
    directive_blob: dict[str, Any],
    target_aspect: str,
    duration_s: float,
) -> str | None:
    """Build a ``zoompan`` filter chain for a smart-camera cut.

    Returns ``None`` when the directive is malformed (missing kind /
    out-of-bounds rects / non-finite duration). Caller falls back to
    the static aspect crop in that case so a single bad directive
    doesn't kill the render.

    Implementation note: we drive zoompan instead of a time-varying
    ``crop=W:H:x:y`` because ffmpeg's crop demands a constant output
    size across the stream — zoompan was designed exactly for this
    "moving window with optional zoom" use case (and we already use
    it for the M8.1 emotion zoom path, so the per-frame d=1 quirk is
    a known-good pattern here).

    For all three kinds we map the directive's normalised
    ``from_rect`` / ``to_rect`` to the zoompan parameters as:

      * Zoom value ``z(t)`` — interpolated between the two rects'
        scale (= 1 / max(w_norm, h_norm)). zoom_in / zoom_out get a
        changing value; pan keeps it constant at ``PAN_SCALE``.
      * Window centre ``(cx(t), cy(t))`` — interpolated between the
        rects' centres. Drives zoompan's ``x``/``y`` (top-left) via
        ``cx*iw - (iw/zoom)/2`` so the focus point lands centred in
        the output frame.

    The output frame is sized to the project target aspect so the
    surrounding stage doesn't have to fix-up dimensions afterwards.
    """
    if target_aspect not in ASPECT_DIMENSIONS:
        return None
    width, height = ASPECT_DIMENSIONS[target_aspect]
    duration_s = max(0.001, float(duration_s))
    total_frames = max(1, int(round(duration_s * VIDEO_FPS)))

    kind = str(directive_blob.get("kind", ""))
    if kind not in SMART_CAMERA_KINDS:
        return None
    try:
        from_rect = tuple(float(v) for v in directive_blob["from_rect"])
        to_rect = tuple(float(v) for v in directive_blob["to_rect"])
    except (KeyError, TypeError, ValueError):
        return None
    if len(from_rect) != 4 or len(to_rect) != 4:
        return None
    fx, fy, fw, fh = from_rect
    tx, ty, tw, th = to_rect
    for v in (fx, fy, fw, fh, tx, ty, tw, th):
        if not (-0.001 <= v <= 1.001):
            return None
    # Convert each rect to (centre_x, centre_y, scale) where scale =
    # 1 / max(w_norm, h_norm) so the smaller window = bigger zoom.
    fw = max(0.05, min(1.0, fw))
    fh = max(0.05, min(1.0, fh))
    tw = max(0.05, min(1.0, tw))
    th = max(0.05, min(1.0, th))
    f_cx = fx + fw / 2.0
    f_cy = fy + fh / 2.0
    t_cx = tx + tw / 2.0
    t_cy = ty + th / 2.0
    f_zoom = 1.0 / max(fw, fh)
    t_zoom = 1.0 / max(tw, th)

    ease = str(directive_blob.get("ease", "linear"))
    if ease not in ("linear", "exp"):
        ease = "linear"

    # Normalised time progress in [0, 1] across the cut. With d=1 and
    # output fps = VIDEO_FPS, ``on`` (= zoompan's "current output
    # frame number") runs 0..total_frames-1.
    t_lin = f"on/{max(1, total_frames - 1)}"
    # Smooth exp-ease for energetic-motion cuts (linear ramp reads as
    # mechanical there); otherwise the linear progress is what we want.
    t_progress = f"(1-exp(-3*{t_lin}))/(1-exp(-3))" if ease == "exp" else t_lin

    # Zoom + position lerp.
    if abs(f_zoom - t_zoom) < 1e-6:
        z_expr = f"{f_zoom:.6f}"
    else:
        z_expr = f"({f_zoom:.6f}+({t_zoom - f_zoom:.6f})*({t_progress}))"
    cx_expr = f"({f_cx:.6f}+({t_cx - f_cx:.6f})*({t_progress}))"
    cy_expr = f"({f_cy:.6f}+({t_cy - f_cy:.6f})*({t_progress}))"

    # Top-left of the source window so the centre lands at (cx*iw,
    # cy*ih). Clamp into [0, iw - iw/zoom] / [0, ih - ih/zoom] so a
    # focus near the source edge doesn't request a window that
    # extends beyond the source bounds (zoompan would silently
    # letterbox in that case).
    x_top_left = f"({cx_expr}*iw - (iw/zoom)/2)"
    y_top_left = f"({cy_expr}*ih - (ih/zoom)/2)"
    x_clamped = f"max(0\\,min(iw - iw/zoom\\,{x_top_left}))"
    y_clamped = f"max(0\\,min(ih - ih/zoom\\,{y_top_left}))"

    return (
        f"zoompan="
        f"z='{z_expr}'"
        f":d=1"
        f":x='{x_clamped}'"
        f":y='{y_clamped}'"
        f":s={width}x{height}"
        f":fps={VIDEO_FPS}"
    )


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
        if out_idx is not None and cmd[out_idx] != "-":
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
    point_track: dict[str, Any] | None = None,
    crop_region: tuple[float, float] | None = None,
    smart_camera_enabled: bool = False,
    stabilize_enabled: bool = False,
) -> bool:
    """Cut + scale-and-crop one segment to a uniform intermediate mp4.

    Returns ``True`` when a dynamic ``crop@reframe`` chain was applied
    (i.e. the segment is now subject-centred via point/custom/YOLO
    tracking), ``False`` when the static aspect crop was used. The
    caller uses this signal to decide whether to also apply vidstab —
    a dynamic-cropped segment is already subject-stabilised, so a
    second vidstab pass would just translate the (now fixed) subject
    back off-centre (v0.23.4 fix).

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

    vf_chain = aspect_filter(target_aspect, crop_region=crop_region)
    # v0.17 — auto-reframe input picks between three sources:
    #   custom_roi  → user-drawn ROI tracked through CSRT
    #   tracking + tracking_object_index  → user-picked YOLO track
    #   tracking only → dominant YOLO track (historic default)
    crop_path = None
    if sendcmd_dir is not None:
        # v0.23 — point_track wins over custom_roi which wins over
        # YOLO tracking. The dispatch reads the same way as the
        # ``tracked_object_index`` sentinel order: -4 (point) → -1
        # (custom_roi) → ≥0 / null (YOLO).
        if point_track:
            crop_path = auto_reframe.compute_crop_path_from_point_track(
                point_track,
                target_aspect=target_aspect,
                asset_start_ms=cut.asset_start_ms,
                asset_end_ms=cut.asset_end_ms,
            )
        elif custom_roi:
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
                smooth_camera_path=tracking_object_index is None,
            )
        if crop_path is not None:
            sendcmd_path = sendcmd_dir / f"reframe_seg_{cut.order:04d}.txt"
            auto_reframe.write_sendcmd_file(crop_path, sendcmd_path)
            target_w, target_h = ASPECT_DIMENSIONS[target_aspect]
            vf_chain = auto_reframe.build_filter_chain(crop_path, sendcmd_path, target_w, target_h)

    # v0.30.16 — opt-in smart camera is literal: when the operator turns
    # it on and a directive exists, it overrides every tracking crop path
    # (automatic YOLO, picked YOLO object, custom ROI, or point track) plus
    # emotion zoompan. Explicit tracking is still useful when Smart Camera
    # is off; with Smart Camera on, the camera move must be visible.
    #
    # Smart-camera cuts are reported as dynamically reframed so the later
    # vidstab stage skips them. Running vidstab after zoompan can interpret
    # the intentional camera move as shake and create a mid-cut correction shove.
    smart_blob = getattr(cut, "smart_camera_json", None)
    smart_chain: str | None = None
    if smart_camera_enabled and isinstance(smart_blob, dict):
        try:
            smart_chain = _smart_camera_filter(smart_blob, target_aspect, duration_s)
        except Exception:  # noqa: BLE001 — never let a single bad directive fail render.
            logger.exception(
                "smart-camera filter build failed for cut %d; falling back to static",
                cut.order,
            )
            smart_chain = None
        if smart_chain is None and smart_blob.get("kind") in SMART_CAMERA_KINDS:
            logger.info(
                "smart-camera: cut %d directive present but filter rejected; static fallback",
                cut.order,
            )
    if smart_chain is not None and crop_path is not None:
        logger.info(
            "smart-camera: cut %d overrides automatic auto-reframe",
            cut.order,
        )
    if smart_chain is not None:
        # The smart-camera filter renders directly to the target
        # canvas, so the static aspect step is redundant. Replace
        # the chain entirely with the zoompan-driven crop.
        vf_chain = smart_chain
    elif _should_zoompan(cut):
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
    return crop_path is not None or smart_chain is not None


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
    point_track_by_asset: dict[int, dict[str, Any]] | None = None,
    crop_region: tuple[float, float] | None = None,
    smart_camera_enabled: bool = False,
    stabilize_enabled: bool = False,
) -> tuple[list[Path], list[bool]]:
    """Cut every segment in the plan; return ``(paths, reframed_flags)``.

    ``reframed_flags[i]`` is ``True`` when segment i was rendered with a
    dynamic crop path (point / custom_roi / YOLO tracking, or AI Smart
    Camera). Callers thread this into ``stabilize_segments`` so a segment
    that's already camera-directed doesn't get a second vidstab pass.

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
    has_any_reframe = (
        bool(tracking_by_asset) or bool(custom_roi_by_asset) or bool(point_track_by_asset)
    )
    if has_any_reframe:
        sendcmd_dir.mkdir(parents=True, exist_ok=True)
    out_paths: list[Path] = []
    reframed_flags: list[bool] = []
    total = len(plan.segments)
    for cut in plan.segments:
        src = asset_paths.get(cut.asset_id)
        if src is None or not Path(src).is_file():
            raise VideoRenderError(f"segment {cut.order}: asset {cut.asset_id} source missing")
        out = intermediate_dir / f"seg_{cut.order:04d}.mp4"
        track = (tracking_by_asset or {}).get(cut.asset_id)
        target_idx = (tracking_target_by_asset or {}).get(cut.asset_id)
        custom_roi = (custom_roi_by_asset or {}).get(cut.asset_id)
        point_track = (point_track_by_asset or {}).get(cut.asset_id)
        # Sentinels disable auto-reframe entirely; defensively clear
        # the inputs so the chain falls back to the static aspect crop.
        if target_idx in (-2, -3):
            track = None
            custom_roi = None
            point_track = None
        reframed = _cut_segment(
            Path(src),
            cut,
            out,
            target_aspect,
            tracking=track,
            sendcmd_dir=sendcmd_dir if has_any_reframe else None,
            tracking_object_index=target_idx
            if (target_idx is not None and target_idx >= 0)
            else None,
            custom_roi=custom_roi if target_idx == -1 else None,
            point_track=point_track if target_idx == -4 else None,
            crop_region=crop_region,
            smart_camera_enabled=smart_camera_enabled,
            stabilize_enabled=stabilize_enabled,
        )
        out_paths.append(out)
        reframed_flags.append(bool(reframed))
        if on_progress is not None:
            on_progress(cut.order + 1, total)
    return out_paths, reframed_flags


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
    skip_indexes: set[int] | None = None,
) -> list[Path]:
    """Run two-pass vidstab over each per-segment intermediate.

    Replaces each ``seg_NNNN.mp4`` in-place by writing a stabilised
    version to ``seg_NNNN.stab.mp4`` and returning the new path list.
    The originals stay on disk until ``cleanup_intermediates`` runs so
    a stabilize bug doesn't lose the un-stabilised render.

    ``skip_indexes`` (v0.23.4) flags positions that already came out of
    ``cut_segments`` with a dynamic ``crop@reframe`` chain applied. On
    those segments the subject is already locked to the output centre,
    so a second vidstab pass would compute a translation off the
    background motion (which the dynamic crop CREATED by holding the
    subject still while the camera panned) and undo the centring.
    Skipped segments are returned at their pre-stabilisation path so
    the concat list stays the same length and order.
    """
    _require_ffmpeg()
    skip = skip_indexes or set()
    out: list[Path] = []
    total = len(intermediate_paths)
    for i, src in enumerate(intermediate_paths):
        if i in skip:
            out.append(src)
        else:
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
    {
        # v0.14.3 default set — assertive variants.
        "wipeleft",
        "slideright",
        "circlecrop",
        # v0.18 — re-introduced for the slow / artistic / commercial style
        # presets. These are valid ffmpeg xfade filter values; the
        # original removal in v0.14.3 was a UX choice ("every reel looked
        # the same"), not a tech limitation. The default style ("custom")
        # still picks from the assertive set above; only the named slow
        # / artistic / commercial presets opt in to these.
        "fade",
        "dissolve",
        "fadeblack",
        "fadewhite",
    }
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
        assert durations_ms is not None
        assert transitions is not None
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
    return text.replace("\n", "\\n")


def _resolve_subtitle_style(
    style: SubtitleStyle | None,
) -> tuple[str, int, str, str, int, str]:
    """Look up the drawtext-ready values for a SubtitleStyle.

    Returns ``(font_path, font_size, font_color, outline_color,
    border_w, y_expr)``. Unknown choice keys fall back to the
    historic defaults so a stale Project row never fails the render.
    """
    s = style or SubtitleStyle()
    font_path = SUBTITLE_FONT_CHOICES.get(s.font, SUBTITLE_FONT_PATH)
    font_size = SUBTITLE_SIZE_CHOICES.get(s.size, SUBTITLE_FONT_SIZE)
    border_w = SUBTITLE_OUTLINE_WIDTH_CHOICES.get(s.outline_width, SUBTITLE_BORDER_W)
    font_color = _hex_to_drawtext_color(s.color)
    outline_color = _hex_to_drawtext_color(s.outline_color)
    if s.position == "top":
        y_expr = f"{SUBTITLE_TOP_OFFSET_PX}"
    elif s.position == "middle":
        y_expr = "(h-text_h)/2"
    else:  # "bottom" (default)
        y_expr = f"h-{SUBTITLE_BOTTOM_OFFSET_PX}-text_h"
    return font_path, font_size, font_color, outline_color, border_w, y_expr


def _build_drawtext_chain(
    cues: list[tuple[float, float, str]],
    style: SubtitleStyle | None = None,
    secondary_cues: list[tuple[float, float, str]] | None = None,
) -> str:
    """Build a comma-chained drawtext filtergraph for primary (+ optional secondary) cues.

    Each filter is gated by ``enable=between(t,start,end)`` so only the
    active cue draws on any given frame. Primary style values are
    resolved off ``style`` (font / size / colour / outline / position);
    when ``style`` is None the historic white-on-black bottom-anchored
    look is used. The optional secondary cue (v0.18 dual-language)
    stacks above the primary using the fixed Noto Sans CJK font in a
    smaller size so a two-line primary still leaves the secondary
    visible above it. Filter ordering: primary first, then secondary,
    so the secondary is the last layer drawn.
    """
    font_path, font_size, font_color, outline_color, border_w, y_expr = _resolve_subtitle_style(
        style
    )
    parts: list[str] = []
    for start_s, end_s, text in cues:
        escaped = _drawtext_escape(text)
        # ``borderw=0`` is the documented "no outline" value but ffmpeg
        # still draws the border colour when border_w == 0 on some
        # builds; gate the bordercolor field too so the user-selected
        # "none" outline really has no edge.
        outline_part = f":borderw={border_w}:bordercolor={outline_color}" if border_w > 0 else ""
        parts.append(
            f"drawtext=fontfile={font_path}"
            f":fontsize={font_size}"
            f":fontcolor={font_color}"
            f"{outline_part}"
            f":x=(w-text_w)/2"
            f":y={y_expr}"
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
    subtitle_style: SubtitleStyle | None = None,
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
    sizing is uniform across canvases now. ``subtitle_style`` is the
    v0.18 user-customised style; ``None`` keeps the historic look.

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
            secondary_cues = _parse_srt_cues(secondary_srt_path.read_text(encoding="utf-8"))
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
        cmd += [
            "-vf",
            _build_drawtext_chain(cues, subtitle_style, secondary_cues or None),
        ]
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
    transitions_enabled: bool = False,
    tracking_by_asset: dict[int, dict[str, Any]] | None = None,
    tracking_target_by_asset: dict[int, int | None] | None = None,
    custom_roi_by_asset: dict[int, dict[str, Any]] | None = None,
    point_track_by_asset: dict[int, dict[str, Any]] | None = None,
    crop_region: tuple[float, float] | None = None,
    smart_camera_enabled: bool = False,
    subtitle_style: SubtitleStyle | None = None,
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
    intermediates, reframed_flags = cut_segments(
        plan,
        asset_paths,
        intermediate_dir,
        target_aspect,
        on_progress=_seg_progress,
        tracking_by_asset=tracking_by_asset,
        tracking_target_by_asset=tracking_target_by_asset,
        custom_roi_by_asset=custom_roi_by_asset,
        point_track_by_asset=point_track_by_asset,
        crop_region=crop_region,
        smart_camera_enabled=smart_camera_enabled,
        stabilize_enabled=stabilize,
    )

    # Stage 1.5 — optional digital stabilization. Replaces each
    # intermediate with a stabilised version before concat. The two-pass
    # vidstab is the slow part of the pipeline so we surface it as its
    # own progress bucket.
    #
    # v0.23.4 — segments that already came out of cut_segments with a
    # dynamic ``crop@reframe`` chain are subject-stabilised by design;
    # running vidstab on top of one would compute a translation off the
    # background motion (the dynamic crop INTRODUCES that motion by
    # holding the subject still while the camera pans) and would push
    # the subject right back off-centre. Skip those positions.
    if stabilize:

        def _stab_progress(done: int, total: int) -> None:
            if on_progress is not None:
                on_progress("stabilize", done, total)

        intermediates = stabilize_segments(
            intermediates,
            intermediate_dir,
            on_progress=_stab_progress,
            skip_indexes={i for i, r in enumerate(reframed_flags) if r},
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
        durations_ms: list[int] | None = [s.asset_end_ms - s.asset_start_ms for s in plan.segments]
        transitions: list[str] | None = [s.transition_to_next for s in plan.segments[:-1]]
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
    has_primary_srt = srt_path is not None and srt_path.is_file() and srt_path.stat().st_size > 0
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
            subtitle_style=subtitle_style,
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


# ---------- watermark / logo overlay (v0.18) ----------


def _watermark_position_xy(position: str) -> tuple[str, str]:
    """Map a 9-grid position name to ``(x_expr, y_expr)`` for ``overlay=``.

    Expressions reference the main video's ``W``/``H`` and the overlay's
    ``w``/``h``, plus a margin variable ``${m}`` that the caller injects.
    Falls back to ``WATERMARK_DEFAULT_POSITION`` when ``position`` is not
    one of the nine recognised anchors so a stale row never makes ffmpeg
    blow up — the overlay just lands in its default spot.
    """
    pos = position if position in WATERMARK_POSITIONS else WATERMARK_DEFAULT_POSITION
    vert, horiz = pos.split("-", 1)
    if horiz == "left":
        x_expr = "${m}"
    elif horiz == "right":
        x_expr = "W-w-${m}"
    else:  # center
        x_expr = "(W-w)/2"
    if vert == "top":
        y_expr = "${m}"
    elif vert == "bottom":
        y_expr = "H-h-${m}"
    else:  # middle
        y_expr = "(H-h)/2"
    return x_expr, y_expr


def _watermark_filter(
    *,
    canvas_w: int,
    canvas_h: int,
    position: str,
    scale: float,
    opacity: float,
) -> str:
    """Build the ``filter_complex`` chain that scales + alpha-blends the
    watermark onto the main video.

    Two filter graphs separated by ``;``:
      1. Logo prep: force RGBA, multiply alpha by ``opacity``, scale to
         ``round(canvas_w * scale)`` keeping aspect.
      2. Overlay: anchor the result onto ``[0:v]`` at the picked grid
         position with a 2 %-of-canvas margin (floored at 12 px).

    Both ``scale`` and ``opacity`` are clamped to their renderer bounds
    so a degenerate row can't request a 5000 px logo or negative alpha.
    """
    scale = max(WATERMARK_SCALE_MIN, min(WATERMARK_SCALE_MAX, float(scale)))
    opacity = max(WATERMARK_OPACITY_MIN, min(WATERMARK_OPACITY_MAX, float(opacity)))
    target_w = max(1, int(round(canvas_w * scale)))
    margin = max(WATERMARK_MARGIN_MIN_PX, int(round(canvas_w * WATERMARK_MARGIN_RATIO)))
    x_expr, y_expr = _watermark_position_xy(position)
    x_expr = x_expr.replace("${m}", str(margin))
    y_expr = y_expr.replace("${m}", str(margin))
    # ``-1`` for the scale height keeps the source aspect; ``flags=lanczos``
    # gives a clean shrink without the moire that bilinear leaves on
    # high-contrast logos.
    logo_chain = (
        f"[1:v]format=rgba,colorchannelmixer=aa={opacity:.4f},scale={target_w}:-1:flags=lanczos[wm]"
    )
    overlay_chain = f"[0:v][wm]overlay={x_expr}:{y_expr}:format=auto[vout]"
    return f"{logo_chain};{overlay_chain}"


def apply_watermark(
    input_path: Path,
    output_path: Path,
    *,
    watermark_path: Path,
    target_aspect: str,
    position: str = WATERMARK_DEFAULT_POSITION,
    scale: float = 0.10,
    opacity: float = 1.0,
) -> None:
    """Re-encode ``input_path`` with the watermark PNG overlaid.

    Single ffmpeg subprocess; audio is stream-copied so this only touches
    the video pass. Encoding knobs match the rest of the pipeline
    (libx264 / crf 20 / faststart) so the file stays consistent with
    what came out of subtitle / BGM stages.
    """
    _require_ffmpeg()
    if target_aspect not in ASPECT_DIMENSIONS:
        raise VideoRenderError(f"watermark: unsupported aspect {target_aspect!r}")
    if not watermark_path.is_file():
        raise VideoRenderError(f"watermark: PNG not found at {watermark_path}")
    if not input_path.is_file() and not _is_fake():
        raise VideoRenderError(f"watermark: input mp4 missing at {input_path}")

    canvas_w, canvas_h = ASPECT_DIMENSIONS[target_aspect]
    filter_complex = _watermark_filter(
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        position=position,
        scale=scale,
        opacity=opacity,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-i",
        str(watermark_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "0:a?",
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
    _run(cmd, timeout_s=WATERMARK_TIMEOUT_S, stage="watermark")


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
    "SUBTITLE_FONT_CHOICES",
    "SUBTITLE_FORCE_STYLE",
    "SUBTITLE_OUTLINE_WIDTH_CHOICES",
    "SUBTITLE_POSITION_CHOICES",
    "SUBTITLE_SIZE_CHOICES",
    "SubtitleStyle",
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
    "WATERMARK_DEFAULT_POSITION",
    "WATERMARK_OPACITY_MAX",
    "WATERMARK_OPACITY_MIN",
    "WATERMARK_POSITIONS",
    "WATERMARK_SCALE_MAX",
    "WATERMARK_SCALE_MIN",
    "WATERMARK_TIMEOUT_S",
    "ZOOMPAN_DYNAMIC_MOTIONS",
    "ZOOMPAN_EMOTIONS",
    "ZOOMPAN_END_ZOOM",
    "apply_watermark",
    "aspect_filter",
    "burn_subtitles",
    "cleanup_intermediates",
    "concat_segments",
    "cut_segments",
    "render",
    "stabilize_segments",
]
