"""Snapshot test for CapCut draft schema parsing."""
from pathlib import Path

import pytest

from tools.capcut_schema_parser.parse_sample import parse_draft

SAMPLE = Path("samples/capcut_draft/mp_sample_001")


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample not present")
def test_parse_sample_returns_top_level_keys() -> None:
    result = parse_draft(SAMPLE)
    assert "version" in result, "draft_content.json should expose a version field"
    assert "tracks" in result, "draft_content.json should expose a tracks list"
    assert isinstance(result["tracks"], list), "tracks must be a list"


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample not present")
def test_parse_sample_has_video_audio_text_tracks() -> None:
    result = parse_draft(SAMPLE)
    track_types = {t.get("type") for t in result["tracks"]}
    # Sample created with 3 video clips + 1 BGM + 1 text layer:
    # we expect at least these track types to appear.
    assert "video" in track_types, f"missing video track; got {track_types}"
    assert "audio" in track_types, f"missing audio track; got {track_types}"
    assert "text" in track_types, f"missing text track; got {track_types}"
