"""Asset source-variant helpers and source-level stabilization.

v0.40.0 keeps the raw upload immutable and adds an optional stabilized
derivative. Analysis, tracking, and render resolve the active source through
this module so coordinates are not mixed across raw/stabilized variants.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Literal

from media_processor.api.config import settings

AssetVariant = Literal["raw", "stabilized"]

RAW_VARIANT: AssetVariant = "raw"
STABILIZED_VARIANT: AssetVariant = "stabilized"
VARIANT_VALUES = frozenset({RAW_VARIANT, STABILIZED_VARIANT})

STABILIZATION_NOT_STARTED = "not_started"
STABILIZATION_PENDING = "pending"
STABILIZATION_RUNNING = "running"
STABILIZATION_DONE = "done"
STABILIZATION_FAILED = "failed"

STABILIZATION_STATUS_VALUES = frozenset(
    {
        STABILIZATION_NOT_STARTED,
        STABILIZATION_PENDING,
        STABILIZATION_RUNNING,
        STABILIZATION_DONE,
        STABILIZATION_FAILED,
    }
)

# Source-level stabilization should be conservative: remove high-frequency
# handheld shake without inventing a new framing path.
STABILIZE_SHAKINESS = 6
STABILIZE_ACCURACY = 9
STABILIZE_STEPSIZE = 6
STABILIZE_SMOOTHING = 12
STABILIZE_ZOOM = 0
STABILIZE_TIMEOUT_S = 60 * 60


class AssetStabilizationError(RuntimeError):
    """Raised when ffmpeg/vidstab cannot produce the stabilized derivative."""


def active_variant(asset: Any) -> AssetVariant:
    value = str(getattr(asset, "active_asset_variant", RAW_VARIANT) or RAW_VARIANT)
    return value if value in VARIANT_VALUES else RAW_VARIANT


def stabilization_status(asset: Any) -> str:
    value = str(getattr(asset, "stabilization_status", STABILIZATION_NOT_STARTED) or "")
    return value if value in STABILIZATION_STATUS_VALUES else STABILIZATION_NOT_STARTED


def stabilized_path_for_asset(asset: Any) -> Path:
    raw = Path(str(asset.file_path))
    safe_stem = raw.stem or f"asset_{asset.id}"
    suffix = raw.suffix or ".mp4"
    return (
        Path(settings.assets_dir)
        / str(asset.project_id)
        / "_stabilized"
        / f"{asset.id}_{safe_stem}.stab{suffix}"
    )


def selected_media_path(asset: Any) -> Path:
    if active_variant(asset) == STABILIZED_VARIANT:
        path = getattr(asset, "stabilized_path", None)
        if path and stabilization_status(asset) == STABILIZATION_DONE:
            return Path(str(path))
    return Path(str(asset.file_path))


def public_asset_url(path: str | Path | None) -> str | None:
    if path is None:
        return None
    try:
        rel = Path(path).resolve().relative_to(Path(settings.assets_dir).resolve())
    except (OSError, ValueError):
        return None
    return "/api/media/assets/" + rel.as_posix()


def variant_urls(asset: Any) -> dict[str, str | None]:
    return {
        RAW_VARIANT: public_asset_url(getattr(asset, "file_path", None)),
        STABILIZED_VARIANT: public_asset_url(getattr(asset, "stabilized_path", None)),
    }


def _is_fake() -> bool:
    return os.getenv("FFMPEG_FAKE", "0") == "1"


def _run(cmd: list[str], *, timeout_s: float, stage: str) -> None:
    if _is_fake():
        out = Path(cmd[-1])
        if out.name != "-":
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise AssetStabilizationError(f"{stage} timed out after {timeout_s:.0f}s") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise AssetStabilizationError(f"{stage} failed: {stderr or exc}") from exc


def stabilize_source(src: Path, dst: Path, scratch_dir: Path) -> None:
    """Run two-pass vidstab over the full source asset into ``dst``."""
    src = Path(src)
    dst = Path(dst)
    scratch_dir = Path(scratch_dir)
    if not src.is_file() and not _is_fake():
        raise AssetStabilizationError(f"source missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    transforms_path = scratch_dir / f"{dst.stem}.trf"
    detect_filter = (
        f"vidstabdetect=stepsize={STABILIZE_STEPSIZE}"
        f":shakiness={STABILIZE_SHAKINESS}"
        f":accuracy={STABILIZE_ACCURACY}"
        f":result={transforms_path.as_posix()}"
    )
    _run(
        [
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
        ],
        timeout_s=STABILIZE_TIMEOUT_S,
        stage="asset-stabilize-detect",
    )
    transform_filter = (
        f"vidstabtransform=input={transforms_path.as_posix()}"
        f":zoom={STABILIZE_ZOOM}"
        f":smoothing={STABILIZE_SMOOTHING}"
        ",unsharp=5:5:0.6:3:3:0.3"
    )
    temp_dst = dst.with_suffix(f".tmp{dst.suffix}")
    _run(
        [
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
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(temp_dst),
        ],
        timeout_s=STABILIZE_TIMEOUT_S,
        stage="asset-stabilize-apply",
    )
    if _is_fake() and not temp_dst.exists():
        temp_dst.write_bytes(b"")
    temp_dst.replace(dst)


__all__ = [
    "RAW_VARIANT",
    "STABILIZED_VARIANT",
    "STABILIZATION_DONE",
    "STABILIZATION_FAILED",
    "STABILIZATION_NOT_STARTED",
    "STABILIZATION_PENDING",
    "STABILIZATION_RUNNING",
    "AssetStabilizationError",
    "active_variant",
    "public_asset_url",
    "selected_media_path",
    "stabilization_status",
    "stabilized_path_for_asset",
    "stabilize_source",
    "variant_urls",
]
