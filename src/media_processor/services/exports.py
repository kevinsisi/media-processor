"""M7.3 — derivative-aspect / resolution exports.

Given an existing 16:9 deliverable mp4, produces a same-content variant
in 9:16 / 4:5 / 1:1 at the user-chosen height. Pure ffmpeg — no
re-planning, no Gemini. The original mp4 stays untouched.

Storage convention: ``${DRAFTS_DIR}/{project_id}/v{N}-{aspect}-{height}p.mp4``
sits next to the original ``v{N}.mp4``. Multiple aspect / height combos
can co-exist for the same draft version.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


VALID_ASPECTS: tuple[str, ...] = ("9:16", "4:5", "1:1")
MIN_HEIGHT = 480
MAX_HEIGHT_CAP = 2160  # 4K — clamp regardless of source so we don't generate impossible files

EXPORT_TIMEOUT_S = 60 * 30  # 30 min — covers a long single-shot at 4K with x264 medium


class ExportError(RuntimeError):
    """Raised on bad input or ffmpeg failure. The orchestrator maps this to a 400/500."""


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    width: int
    height: int
    aspect: str


def _aspect_to_wh_ratio(aspect: str) -> tuple[int, int]:
    if aspect not in VALID_ASPECTS:
        raise ExportError(f"unsupported aspect {aspect!r}; expected one of {VALID_ASPECTS}")
    w_s, h_s = aspect.split(":")
    return int(w_s), int(h_s)


def _resolve_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise ExportError("ffmpeg not found on PATH")
    return exe


def _compute_output_size(aspect: str, height: int) -> tuple[int, int]:
    """Return (width, height) for the target aspect.

    Width is derived from height to preserve the requested aspect; both
    are forced even (libx264 yuv420p requires it). Height is clamped to
    the [MIN_HEIGHT, MAX_HEIGHT_CAP] envelope.
    """
    if height < MIN_HEIGHT:
        raise ExportError(f"height {height} below minimum {MIN_HEIGHT}")
    if height > MAX_HEIGHT_CAP:
        raise ExportError(f"height {height} above cap {MAX_HEIGHT_CAP}")
    a_w, a_h = _aspect_to_wh_ratio(aspect)
    width = int(round(height * a_w / a_h))
    # Force even dimensions (yuv420p requirement).
    if width % 2:
        width -= 1
    if height % 2:
        height -= 1
    return width, height


def export_render(
    input_path: Path,
    output_path: Path,
    *,
    aspect: str,
    height: int,
) -> ExportResult:
    """Run ffmpeg to produce a derivative file at the given aspect / height.

    Filter chain:
      1. ``scale=W:H:force_original_aspect_ratio=increase`` — scale up so the
         shorter side meets the target dim.
      2. ``crop=W:H`` — centre-crop the longer side off.
      3. ``setsar=1`` — square pixels.

    Audio is stream-copied; video is re-encoded with libx264 at preset
    medium / crf 22. ``-y`` overwrites previous exports of the same
    aspect+height combo so re-running is idempotent.

    v0.18 note: the watermark / brand-logo overlay is baked into the
    *source* ``v{N}.mp4`` by the orchestrator, so it travels with the
    source through this re-encode. When the export aspect differs from
    the source's render aspect the centre-crop here can clip the
    watermark — the picker UI calls this out when the user opens the
    export sheet on a watermarked draft.
    """
    if not input_path.is_file():
        raise ExportError(f"input mp4 not found: {input_path}")
    target_w, target_h = _compute_output_size(aspect, height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")

    vf = (
        f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},setsar=1"
    )
    cmd = [
        _resolve_ffmpeg(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(tmp_path),
    ]

    logger.info(
        "export_render: %s -> %s (aspect=%s height=%d => %dx%d)",
        input_path,
        output_path,
        aspect,
        height,
        target_w,
        target_h,
    )
    proc = subprocess.run(  # noqa: S603 — args are fully validated above
        cmd,
        capture_output=True,
        text=True,
        timeout=EXPORT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise ExportError(
            f"ffmpeg export failed (rc={proc.returncode}): {proc.stderr.strip()[:400]}"
        )

    os.replace(tmp_path, output_path)
    return ExportResult(
        output_path=output_path,
        width=target_w,
        height=target_h,
        aspect=aspect,
    )


def derive_filename(version: int, aspect: str, height: int) -> str:
    """Filename for ``v{N}-{aspect}-{height}p.mp4``. Aspect ``:`` → ``x``."""
    safe_aspect = aspect.replace(":", "x")
    return f"v{version}-{safe_aspect}-{height}p.mp4"


__all__ = [
    "EXPORT_TIMEOUT_S",
    "ExportError",
    "ExportResult",
    "MAX_HEIGHT_CAP",
    "MIN_HEIGHT",
    "VALID_ASPECTS",
    "derive_filename",
    "export_render",
]
