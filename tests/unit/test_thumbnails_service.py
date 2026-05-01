"""Unit tests for services.thumbnails — the keyframe-gallery extractor."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from media_processor.services import thumbnails as thumbs


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


@pytest.fixture()
def synthetic_video(tmp_path: Path) -> Path:
    """Generate a tiny 4-second test video via ffmpeg's testsrc filter."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not available on this host")
    out = tmp_path / "in.mp4"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=4:size=320x240:rate=10",
        "-pix_fmt",
        "yuv420p",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def test_seek_seconds_for_zero_duration() -> None:
    assert thumbs._seek_seconds_for(0, 0.5) == 0.0


def test_seek_seconds_for_quarter() -> None:
    assert thumbs._seek_seconds_for(20_000, 0.25) == pytest.approx(5.0)


def test_has_complete_set_false_when_dir_missing(tmp_path: Path) -> None:
    assert not thumbs.has_complete_set(tmp_path, asset_id=999)


def test_generate_returns_video_missing_when_path_invalid(tmp_path: Path) -> None:
    result = thumbs.generate(
        asset_id=1,
        video_path=tmp_path / "nope.mp4",
        duration_ms=1000,
        thumbnails_root=tmp_path / "thumbs",
    )
    assert result.failed_reason == "video-missing"
    assert result.frames_written == 0


def test_generate_returns_duration_zero(tmp_path: Path, synthetic_video: Path) -> None:
    result = thumbs.generate(
        asset_id=2,
        video_path=synthetic_video,
        duration_ms=0,
        thumbnails_root=tmp_path / "thumbs",
    )
    assert result.failed_reason == "duration-zero"


def test_generate_writes_full_set(tmp_path: Path, synthetic_video: Path) -> None:
    thumb_root = tmp_path / "thumbs"
    result = thumbs.generate(
        asset_id=42,
        video_path=synthetic_video,
        duration_ms=4_000,
        thumbnails_root=thumb_root,
    )
    assert result.failed_reason is None
    assert result.frames_written == thumbs.FRAME_COUNT
    assert result.frames_skipped == 0
    assert thumbs.has_complete_set(thumb_root, 42)
    files = thumbs.list_existing_frames(thumb_root, 42)
    assert len(files) == thumbs.FRAME_COUNT
    for f in files:
        assert f.is_file()
        assert f.stat().st_size > 0
        assert f.read_bytes()[:3] == b"\xff\xd8\xff"  # JPEG SOI


def test_generate_idempotent_skip(tmp_path: Path, synthetic_video: Path) -> None:
    thumb_root = tmp_path / "thumbs"
    first = thumbs.generate(
        asset_id=7,
        video_path=synthetic_video,
        duration_ms=4_000,
        thumbnails_root=thumb_root,
    )
    assert first.frames_written == thumbs.FRAME_COUNT

    second = thumbs.generate(
        asset_id=7,
        video_path=synthetic_video,
        duration_ms=4_000,
        thumbnails_root=thumb_root,
    )
    assert second.frames_written == 0
    assert second.frames_skipped == thumbs.FRAME_COUNT


def test_generate_force_overwrites(tmp_path: Path, synthetic_video: Path) -> None:
    thumb_root = tmp_path / "thumbs"
    thumbs.generate(
        asset_id=11,
        video_path=synthetic_video,
        duration_ms=4_000,
        thumbnails_root=thumb_root,
    )
    forced = thumbs.generate(
        asset_id=11,
        video_path=synthetic_video,
        duration_ms=4_000,
        thumbnails_root=thumb_root,
        force=True,
    )
    assert forced.frames_written == thumbs.FRAME_COUNT
    assert forced.frames_skipped == 0


def test_list_existing_frames_sorted(tmp_path: Path, synthetic_video: Path) -> None:
    thumb_root = tmp_path / "thumbs"
    thumbs.generate(
        asset_id=8,
        video_path=synthetic_video,
        duration_ms=4_000,
        thumbnails_root=thumb_root,
    )
    files = thumbs.list_existing_frames(thumb_root, 8)
    indices = [int(f.name[len("frame_") : -len(".jpg")]) for f in files]
    assert indices == list(range(thumbs.FRAME_COUNT))
