"""Unit tests for services.video_renderer using FFMPEG_FAKE=1."""

from __future__ import annotations

from pathlib import Path

import pytest

from media_processor.services import video_renderer
from media_processor.services.edit_planner import CutPlan, CutPlanSegment


@pytest.fixture(autouse=True)
def fake_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every test in this module into the FFMPEG_FAKE branch."""
    monkeypatch.setenv("FFMPEG_FAKE", "1")


def test_aspect_filter_known_ratios() -> None:
    out = video_renderer.aspect_filter("9:16")
    assert "scale=1080:1920" in out
    assert "crop=1080:1920" in out
    assert "setsar=1" in out


def test_aspect_filter_rejects_unknown_ratio() -> None:
    with pytest.raises(video_renderer.VideoRenderError):
        video_renderer.aspect_filter("21:9")


def test_cut_segments_writes_one_file_per_segment(tmp_path: Path) -> None:
    src = tmp_path / "asset.mp4"
    src.write_bytes(b"fake")
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=2_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(0, 1, 0, 1_000, "scripted", "r1"),
            CutPlanSegment(1, 1, 1_000, 2_000, "improv", "r2"),
        ),
    )
    intermediate_dir = tmp_path / "out"
    paths = video_renderer.cut_segments(
        plan,
        asset_paths={1: src},
        intermediate_dir=intermediate_dir,
        target_aspect="9:16",
    )
    assert [p.name for p in paths] == ["seg_0000.mp4", "seg_0001.mp4"]
    for p in paths:
        assert p.is_file()


def test_cut_segments_missing_source_raises(tmp_path: Path) -> None:
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=1_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(CutPlanSegment(0, 99, 0, 1_000, "improv", ""),),
    )
    with pytest.raises(video_renderer.VideoRenderError):
        video_renderer.cut_segments(
            plan,
            asset_paths={},  # no source paths
            intermediate_dir=tmp_path / "out",
            target_aspect="9:16",
        )


def test_concat_writes_list_file(tmp_path: Path) -> None:
    intermediate = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for p in intermediate:
        p.write_bytes(b"")
    output = tmp_path / "final.mp4"
    list_path = tmp_path / "concat.txt"
    video_renderer.concat_segments(intermediate, output, list_path)
    txt = list_path.read_text(encoding="utf-8")
    assert "a.mp4" in txt
    assert "b.mp4" in txt
    assert output.is_file()  # FFMPEG_FAKE writes an empty file


def test_concat_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(video_renderer.VideoRenderError):
        video_renderer.concat_segments([], tmp_path / "out.mp4", tmp_path / "list.txt")


def test_zoompan_filter_emits_canvas_size_and_increment() -> None:
    """Phase 8.1 — zoompan chain ends at ZOOMPAN_END_ZOOM and matches canvas size."""
    chain = video_renderer._zoompan_filter("9:16", duration_s=2.0)
    assert "zoompan=" in chain
    # Canvas matches the 9:16 portrait dimensions.
    assert "s=1080x1920" in chain
    # End zoom must be the documented ceiling.
    assert f"{video_renderer.ZOOMPAN_END_ZOOM}" in chain
    # FPS pinned to VIDEO_FPS so zoompan doesn't resample mid-clip.
    assert f"fps={video_renderer.ZOOMPAN_FPS}" in chain


def test_zoompan_filter_uses_d_eq_one_to_avoid_freeze() -> None:
    """Each input frame must produce ONE output frame so the underlying
    video keeps playing while the zoom progresses. ``d=total_frames``
    (the previous value) holds the first input frame for the entire
    clip, which is the "frozen photo" failure users reported on M8.1.
    """
    chain = video_renderer._zoompan_filter("9:16", duration_s=2.0)
    assert ":d=1:" in chain or chain.endswith(":d=1") or ":d=1," in chain


def test_should_zoompan_skips_static_no_face_clip() -> None:
    """Dominant emotion ``happy`` alone is not enough — a static clip
    without a face during the chosen span gets no zoompan, otherwise the
    zoom layers on top of effectively still video and reads as frozen.
    """
    cut = CutPlanSegment(
        order=0,
        asset_id=1,
        asset_start_ms=0,
        asset_end_ms=2_000,
        source_kind="improv",
        reason="",
        dominant_emotion="happy",
        dominant_motion="static",
        has_face=False,
    )
    assert video_renderer._should_zoompan(cut) is False


def test_should_zoompan_when_face_present_even_on_static() -> None:
    cut = CutPlanSegment(
        order=0,
        asset_id=1,
        asset_start_ms=0,
        asset_end_ms=2_000,
        source_kind="improv",
        reason="",
        dominant_emotion="happy",
        dominant_motion="static",
        has_face=True,
    )
    assert video_renderer._should_zoompan(cut) is True


def test_should_zoompan_when_motion_is_dynamic() -> None:
    cut = CutPlanSegment(
        order=0,
        asset_id=1,
        asset_start_ms=0,
        asset_end_ms=2_000,
        source_kind="improv",
        reason="",
        dominant_emotion="surprised",
        dominant_motion="pan",
        has_face=False,
    )
    assert video_renderer._should_zoompan(cut) is True


def test_should_zoompan_skips_non_dynamic_emotion() -> None:
    cut = CutPlanSegment(
        order=0,
        asset_id=1,
        asset_start_ms=0,
        asset_end_ms=2_000,
        source_kind="improv",
        reason="",
        dominant_emotion="serious",
        dominant_motion="pan",
        has_face=True,
    )
    # No matter how dynamic the rest is, ``serious`` / ``neutral`` keeps
    # the camera locked off — that's the M8.1 design intent.
    assert video_renderer._should_zoompan(cut) is False


def test_circlecrop_in_transition_whitelist() -> None:
    """Phase 8.1 — emotion-shift transitions resolve to circlecrop, not the default."""
    assert "circlecrop" in video_renderer.VALID_TRANSITIONS
    assert video_renderer._safe_transition("circlecrop") == "circlecrop"


def test_render_end_to_end_fake(tmp_path: Path) -> None:
    src = tmp_path / "asset.mp4"
    src.write_bytes(b"fake")
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=1_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(CutPlanSegment(0, 1, 0, 1_000, "improv", ""),),
    )
    output_path = tmp_path / "drafts" / "1" / "v1.mp4"
    srt_path = tmp_path / "drafts" / "1" / "v1.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")

    progress: list[tuple[str, int, int]] = []

    result = video_renderer.render(
        plan,
        draft_id=1,
        target_aspect="9:16",
        asset_paths={1: src},
        output_path=output_path,
        srt_path=srt_path,
        scratch_dir=tmp_path / "scratch",
        on_progress=lambda stage, done, total: progress.append((stage, done, total)),
    )
    assert result.output_path == output_path
    assert result.segment_count == 1
    assert output_path.is_file()
    # All three stages must have fired their done event.
    stages = [p[0] for p in progress]
    assert "cut" in stages
    assert "concat" in stages
    assert "subtitles" in stages
