"""FFmpeg-driven keyframe gallery extractor.

Produces a fixed-size set of evenly-distributed JPEG previews per Asset so the
operator can disambiguate clips at a glance on the analysis page. The first
frame of two takes is often identical (locked-off intro card / slate); frames
sampled from across the duration diverge enough to identify the clip.

Pure subprocess wrapper around ffmpeg — no third-party deps. Safe to run from
the API container as long as the ffmpeg binary is on PATH.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Five keyframes evenly distributed across the clip. The endpoints are
# nudged inward (10 % / 90 %) to avoid black leader / outro frames that some
# editors prepend.
FRAME_PERCENTAGES: tuple[float, ...] = (0.10, 0.30, 0.50, 0.70, 0.90)
FRAME_COUNT: int = len(FRAME_PERCENTAGES)
FRAME_WIDTH_PX: int = 320
JPEG_QUALITY: int = 80  # ffmpeg -q:v 1..31, but we map via mjpeg quantiser below
MJPEG_QSCALE: int = 5  # roughly equivalent to JPEG quality 80 (lower = better)
PER_FRAME_TIMEOUT_S: float = 15.0
WHOLE_ASSET_TIMEOUT_S: float = 60.0


@dataclass(frozen=True)
class ThumbnailResult:
    asset_id: int
    frames_written: int
    frames_skipped: int
    failed_reason: str | None


def asset_thumb_dir(thumbnails_root: str | Path, asset_id: int) -> Path:
    return Path(thumbnails_root) / str(asset_id)


def frame_path(thumbnails_root: str | Path, asset_id: int, index: int) -> Path:
    return asset_thumb_dir(thumbnails_root, asset_id) / f"frame_{index}.jpg"


def expected_frame_paths(thumbnails_root: str | Path, asset_id: int) -> list[Path]:
    return [frame_path(thumbnails_root, asset_id, i) for i in range(FRAME_COUNT)]


def has_complete_set(thumbnails_root: str | Path, asset_id: int) -> bool:
    """True when every expected frame file already exists and is non-empty."""
    for p in expected_frame_paths(thumbnails_root, asset_id):
        if not p.is_file() or p.stat().st_size == 0:
            return False
    return True


def list_existing_frames(thumbnails_root: str | Path, asset_id: int) -> list[Path]:
    """Return the existing frame files for an asset, sorted by index."""
    d = asset_thumb_dir(thumbnails_root, asset_id)
    if not d.is_dir():
        return []
    files: list[tuple[int, Path]] = []
    for entry in d.iterdir():
        if not entry.is_file() or not entry.name.startswith("frame_"):
            continue
        if not entry.name.endswith(".jpg"):
            continue
        stem = entry.name[len("frame_") : -len(".jpg")]
        try:
            idx = int(stem)
        except ValueError:
            continue
        files.append((idx, entry))
    files.sort(key=lambda x: x[0])
    return [p for _, p in files]


def _seek_seconds_for(duration_ms: int, percentage: float) -> float:
    """Compute the ffmpeg -ss seek (seconds) for a given percentage."""
    duration_s = max(0.0, duration_ms / 1000.0)
    return max(0.0, duration_s * percentage)


def _run_ffmpeg_seek(
    video_path: Path,
    out_path: Path,
    seek_s: float,
) -> bool:
    """Run a single ffmpeg seek-and-snap. Returns True on success."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.jpg")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        # Place -ss before -i for fast (keyframe-accurate) seek; for a 320 px
        # preview we don't need frame-accurate decoding.
        "-ss",
        f"{seek_s:.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        f"scale={FRAME_WIDTH_PX}:-2",
        "-q:v",
        str(MJPEG_QSCALE),
        str(tmp),
    ]
    try:
        subprocess.run(
            cmd,
            capture_output=True,
            timeout=PER_FRAME_TIMEOUT_S,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        logger.warning("ffmpeg seek failed for %s @ %.2fs: %s", video_path, seek_s, exc)
        tmp.unlink(missing_ok=True)
        return False
    if not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(out_path)
    return True


def generate(
    asset_id: int,
    video_path: str | Path,
    duration_ms: int,
    thumbnails_root: str | Path,
    *,
    force: bool = False,
) -> ThumbnailResult:
    """Generate the 5-frame gallery for an asset.

    Idempotent: existing valid frames are skipped unless ``force=True``. On
    any unrecoverable error (ffmpeg missing, video unreadable) returns a
    result with ``failed_reason`` set instead of raising — callers in the
    upload path treat this as best-effort.
    """
    src = Path(video_path)
    if not src.is_file():
        return ThumbnailResult(asset_id, 0, 0, "video-missing")
    if shutil.which("ffmpeg") is None:
        return ThumbnailResult(asset_id, 0, 0, "ffmpeg-missing")
    if duration_ms <= 0:
        return ThumbnailResult(asset_id, 0, 0, "duration-zero")

    written = 0
    skipped = 0
    failed: str | None = None
    for index, percentage in enumerate(FRAME_PERCENTAGES):
        out = frame_path(thumbnails_root, asset_id, index)
        if not force and out.is_file() and out.stat().st_size > 0:
            skipped += 1
            continue
        seek_s = _seek_seconds_for(duration_ms, percentage)
        ok = _run_ffmpeg_seek(src, out, seek_s)
        if ok:
            written += 1
        else:
            # Continue trying remaining frames so a single bad seek doesn't
            # leave the whole asset blank — partial galleries still help.
            failed = "ffmpeg-error"
    return ThumbnailResult(asset_id, written, skipped, failed)


__all__ = [
    "FRAME_COUNT",
    "FRAME_PERCENTAGES",
    "FRAME_WIDTH_PX",
    "PER_FRAME_TIMEOUT_S",
    "ThumbnailResult",
    "WHOLE_ASSET_TIMEOUT_S",
    "asset_thumb_dir",
    "expected_frame_paths",
    "frame_path",
    "generate",
    "has_complete_set",
    "list_existing_frames",
]
