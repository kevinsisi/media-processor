"""Tests for the CapCut draft writer."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, cast

import pytest

from media_processor.services.capcut_writer import (
    CapCutDraftWriter,
    WriterBGM,
    WriterCaption,
    WriterDraftMeta,
    WriterSegment,
)


def _meta() -> WriterDraftMeta:
    return WriterDraftMeta(
        name="carsmeet-Phantom-0428",
        profile_name="carsmeet-luxury",
        target_duration_ms=30000,
    )


def _segments(n: int = 3) -> list[WriterSegment]:
    return [
        WriterSegment(
            order=i,
            asset_path=f"/Volumes/MediaProcessor/assets/{i}.mp4",
            asset_start_ms=i * 1000,
            asset_end_ms=i * 1000 + 800,
            on_timeline_start_ms=i * 1000,
            on_timeline_end_ms=i * 1000 + 1000,
        )
        for i in range(n)
    ]


def _read_content(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf, zf.open("draft_content.json") as f:
        return cast(dict[str, Any], json.loads(f.read().decode("utf-8")))


def test_writer_emits_zip_with_required_entries(tmp_path: Path) -> None:
    out = tmp_path / "draft.zip"
    CapCutDraftWriter().write(_meta(), _segments(), out)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert names == {"draft_content.json", "draft_meta_info.json"}


def test_writer_includes_schema_version_marker(tmp_path: Path) -> None:
    out = tmp_path / "draft.zip"
    CapCutDraftWriter().write(_meta(), _segments(), out)
    content = _read_content(out)
    assert content["version"] == CapCutDraftWriter.SCHEMA_VERSION


def test_video_only_draft_has_one_track(tmp_path: Path) -> None:
    out = tmp_path / "draft.zip"
    CapCutDraftWriter().write(_meta(), _segments(), out)
    content = _read_content(out)
    tracks = content["tracks"]
    assert isinstance(tracks, list)
    assert len(tracks) == 1
    assert tracks[0]["type"] == "video"
    assert len(tracks[0]["segments"]) == 3


def test_full_draft_has_video_audio_text(tmp_path: Path) -> None:
    out = tmp_path / "draft.zip"
    CapCutDraftWriter().write(
        _meta(),
        _segments(),
        out,
        bgm=WriterBGM(file_path="/Volumes/MediaProcessor/bgm/track.mp3"),
        captions=[WriterCaption(start_ms=0, end_ms=1000, text="hello")],
    )
    content = _read_content(out)
    types = {t["type"] for t in content["tracks"]}
    assert types == {"video", "audio", "text"}


def test_segment_paths_preserved_verbatim(tmp_path: Path) -> None:
    out = tmp_path / "draft.zip"
    seg = WriterSegment(
        order=0,
        asset_path="/Volumes/MediaProcessor/assets/foo.mp4",
        asset_start_ms=0,
        asset_end_ms=1000,
        on_timeline_start_ms=0,
        on_timeline_end_ms=1000,
    )
    CapCutDraftWriter().write(_meta(), [seg], out)
    content = _read_content(out)
    video = content["tracks"][0]
    assert video["segments"][0]["source_path"] == "/Volumes/MediaProcessor/assets/foo.mp4"
    # No media bytes inside zip.
    with zipfile.ZipFile(out) as zf:
        media = [n for n in zf.namelist() if not n.endswith(".json")]
    assert media == []


def test_writer_is_idempotent(tmp_path: Path) -> None:
    out_a = tmp_path / "a.zip"
    out_b = tmp_path / "b.zip"
    writer = CapCutDraftWriter()
    writer.write(_meta(), _segments(), out_a)
    writer.write(_meta(), _segments(), out_b)
    with zipfile.ZipFile(out_a) as za, zipfile.ZipFile(out_b) as zb:
        a_bytes = za.read("draft_content.json")
        b_bytes = zb.read("draft_content.json")
    assert a_bytes == b_bytes


@pytest.mark.parametrize("n", [1, 5, 30])
def test_segment_count_matches_input(tmp_path: Path, n: int) -> None:
    out = tmp_path / "draft.zip"
    CapCutDraftWriter().write(_meta(), _segments(n), out)
    content = _read_content(out)
    assert len(content["tracks"][0]["segments"]) == n
