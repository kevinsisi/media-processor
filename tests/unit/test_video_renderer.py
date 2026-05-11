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


def test_aspect_filter_landscape() -> None:
    """v0.29.0 — 16:9 is the new horizontal output aspect."""
    out = video_renderer.aspect_filter("16:9")
    assert "scale=1920:1080" in out
    assert "crop=1920:1080" in out
    assert "setsar=1" in out


def test_aspect_filter_drops_4_5_and_1_1() -> None:
    """v0.29.0 — 4:5 and 1:1 are removed; renderer rejects them."""
    with pytest.raises(video_renderer.VideoRenderError):
        video_renderer.aspect_filter("4:5")
    with pytest.raises(video_renderer.VideoRenderError):
        video_renderer.aspect_filter("1:1")


def test_aspect_filter_rejects_unknown_ratio() -> None:
    with pytest.raises(video_renderer.VideoRenderError):
        video_renderer.aspect_filter("21:9")


def test_aspect_filter_centre_crop_omits_xy() -> None:
    """v0.29.0 — when crop_region is None or centre we skip the explicit
    x/y expression so the chain stays close to the pre-0.29 form."""
    none_chain = video_renderer.aspect_filter("16:9")
    centre_chain = video_renderer.aspect_filter("16:9", crop_region=(0.5, 0.5))
    assert "crop=1920:1080," in none_chain
    assert "crop=1920:1080," in centre_chain
    # Neither emits the expression form.
    assert "max(0" not in none_chain
    assert "max(0" not in centre_chain


def test_aspect_filter_off_centre_emits_clamped_expressions() -> None:
    """v0.29.0 — off-centre anchors emit a clamped x/y expression."""
    chain = video_renderer.aspect_filter("16:9", crop_region=(0.5, 0.0))
    # The y-expression is the one that moved.
    assert "crop=1920:1080:" in chain
    assert "max(0" in chain
    assert "in_h-1080" in chain


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
    paths, reframed_flags = video_renderer.cut_segments(
        plan,
        asset_paths={1: src},
        intermediate_dir=intermediate_dir,
        target_aspect="9:16",
    )
    assert [p.name for p in paths] == ["seg_0000.mp4", "seg_0001.mp4"]
    for p in paths:
        assert p.is_file()
    # No tracking inputs were supplied → every segment uses the static
    # aspect crop, so the reframed-flag list is all False.
    assert reframed_flags == [False, False]


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


# ----- v0.30.0 — AI Smart Camera filter -----


def test_smart_camera_filter_zoom_in_emits_zoompan() -> None:
    """zoom_in directive lands as a zoompan filter with the canvas dims."""
    chain = video_renderer._smart_camera_filter(
        {
            "kind": "zoom_in",
            "from_rect": [0.0, 0.0, 1.0, 1.0],
            "to_rect": [0.30, 0.30, 0.40, 0.40],
            "ease": "linear",
        },
        "9:16",
        duration_s=2.0,
    )
    assert chain is not None
    assert "zoompan=" in chain
    assert "s=1080x1920" in chain
    # d=1 mirrors the M8.1 emotion-zoompan fix so the underlying
    # video keeps playing while the camera moves.
    assert ":d=1:" in chain or chain.endswith(":d=1") or ":d=1," in chain


def test_smart_camera_filter_pan_uses_constant_zoom() -> None:
    """Pan keeps zoom constant — no interpolation expression needed for z."""
    chain = video_renderer._smart_camera_filter(
        {
            "kind": "pan",
            "from_rect": [0.0, 0.40, 0.20, 0.20],
            "to_rect": [0.80, 0.40, 0.20, 0.20],
            "ease": "linear",
        },
        "16:9",
        duration_s=3.0,
    )
    assert chain is not None
    assert "zoompan=" in chain
    assert "s=1920x1080" in chain


def test_smart_camera_filter_rejects_malformed_directive() -> None:
    """Bad inputs return None so the renderer falls back to the static path."""
    assert video_renderer._smart_camera_filter({}, "9:16", 1.0) is None
    assert (
        video_renderer._smart_camera_filter(
            {"kind": "wibble", "from_rect": [0, 0, 1, 1], "to_rect": [0, 0, 1, 1]},
            "9:16",
            1.0,
        )
        is None
    )
    # Out-of-range rect coords are rejected, not silently clamped past 1.
    assert (
        video_renderer._smart_camera_filter(
            {"kind": "zoom_in", "from_rect": [0, 0, 1, 1], "to_rect": [0, 0, 5, 5]},
            "9:16",
            1.0,
        )
        is None
    )


def test_smart_camera_filter_uses_exp_ease_when_requested() -> None:
    """ease=exp injects an exp-shaped progress expression."""
    chain = video_renderer._smart_camera_filter(
        {
            "kind": "zoom_in",
            "from_rect": [0.0, 0.0, 1.0, 1.0],
            "to_rect": [0.40, 0.40, 0.20, 0.20],
            "ease": "exp",
        },
        "9:16",
        duration_s=2.0,
    )
    assert chain is not None
    assert "exp(" in chain  # exp ease curve appears in the progress expr.


def test_smart_camera_sync_frame_picks_beat_near_visual_hit() -> None:
    frame = video_renderer._smart_camera_sync_frame(
        duration_s=3.0,
        timeline_start_s=10.0,
        beat_grid_s=[10.5, 11.2, 12.4, 12.9],
    )

    # 80% through a 3 s cut starting at 10 s is 12.4 s.
    assert frame == 72


def test_smart_camera_filter_can_finish_move_on_bgm_beat() -> None:
    chain = video_renderer._smart_camera_filter(
        {
            "kind": "zoom_in",
            "from_rect": [0.0, 0.0, 1.0, 1.0],
            "to_rect": [0.40, 0.40, 0.20, 0.20],
            "ease": "linear",
        },
        "9:16",
        duration_s=3.0,
        timeline_start_s=10.0,
        beat_grid_s=[12.4],
    )

    assert chain is not None
    assert "min(1\\,on/72)" in chain


def test_smart_camera_overrides_emotion_zoompan(tmp_path: Path) -> None:
    """When a smart-camera directive is present AND the cut is also
    eligible for emotion zoompan (happy + face), smart-camera wins.
    Test guarantees the renderer's internal mutex picks the smart-
    camera filter for that cut without raising."""
    src = tmp_path / "asset.mp4"
    src.write_bytes(b"fake")
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=1_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(
                0,
                1,
                0,
                1_000,
                "improv",
                "",
                dominant_emotion="happy",
                dominant_motion="static",
                has_face=True,
                smart_camera_json={
                    "kind": "zoom_in",
                    "from_rect": [0.0, 0.0, 1.0, 1.0],
                    "to_rect": [0.30, 0.30, 0.40, 0.40],
                    "ease": "linear",
                },
            ),
        ),
    )
    out_dir = tmp_path / "out"
    paths, reframed = video_renderer.cut_segments(
        plan,
        asset_paths={1: src},
        intermediate_dir=out_dir,
        target_aspect="9:16",
        smart_camera_enabled=True,
    )
    assert len(paths) == 1
    assert paths[0].is_file()
    # Smart Camera is already a directed camera path, so vidstab must skip it.
    assert reframed == [True]


def test_smart_camera_skips_later_vidstab_when_stabilize_active(tmp_path: Path) -> None:
    """When stabilize is on, Smart Camera still applies and skips vidstab."""
    src = tmp_path / "asset.mp4"
    src.write_bytes(b"fake")
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=1_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(
                0,
                1,
                0,
                1_000,
                "improv",
                "",
                smart_camera_json={
                    "kind": "zoom_in",
                    "from_rect": [0.0, 0.0, 1.0, 1.0],
                    "to_rect": [0.30, 0.30, 0.40, 0.40],
                    "ease": "linear",
                },
            ),
        ),
    )
    out_dir = tmp_path / "out"
    paths, reframed = video_renderer.cut_segments(
        plan,
        asset_paths={1: src},
        intermediate_dir=out_dir,
        target_aspect="9:16",
        smart_camera_enabled=True,
        stabilize_enabled=True,
    )
    assert len(paths) == 1
    assert reframed == [True]


def test_smart_camera_overrides_automatic_auto_reframe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smart Camera should not be silently masked by default YOLO auto-reframe."""
    src = tmp_path / "asset.mp4"
    src.write_bytes(b"fake")
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=1_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(
                0,
                1,
                0,
                1_000,
                "improv",
                "",
                smart_camera_json={
                    "kind": "pan",
                    "from_rect": [0.0, 0.0, 1.0, 1.0],
                    "to_rect": [0.20, 0.20, 0.60, 0.60],
                    "ease": "linear",
                },
            ),
        ),
    )
    captured_filters: list[str] = []

    monkeypatch.setattr(
        video_renderer.auto_reframe,
        "compute_crop_path",
        lambda *args, **kwargs: [(0, 0, 1080, 1920)],
    )
    monkeypatch.setattr(video_renderer.auto_reframe, "write_sendcmd_file", lambda *args: None)
    monkeypatch.setattr(
        video_renderer.auto_reframe,
        "build_filter_chain",
        lambda *args, **kwargs: "AUTO_REFRAME_CHAIN",
    )
    monkeypatch.setattr(
        video_renderer,
        "_smart_camera_filter",
        lambda *args, **kwargs: "SMART_CAMERA_CHAIN",
    )

    def fake_run(cmd: list[str], *, timeout_s: float, stage: str) -> None:
        captured_filters.append(cmd[cmd.index("-vf") + 1])
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"")

    monkeypatch.setattr(video_renderer, "_run", fake_run)

    paths, reframed = video_renderer.cut_segments(
        plan,
        asset_paths={1: src},
        intermediate_dir=tmp_path / "out",
        target_aspect="9:16",
        tracking_by_asset={1: {"frames": []}},
        smart_camera_enabled=True,
    )

    assert len(paths) == 1
    assert reframed == [True]
    assert captured_filters == ["SMART_CAMERA_CHAIN"]


def test_smart_camera_overrides_explicit_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Smart Camera is checked, even picked tracking targets must not mask it."""
    src = tmp_path / "asset.mp4"
    src.write_bytes(b"fake")
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=1_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(
                0,
                1,
                0,
                1_000,
                "improv",
                "",
                smart_camera_json={
                    "kind": "pan",
                    "from_rect": [0.0, 0.0, 1.0, 1.0],
                    "to_rect": [0.20, 0.20, 0.60, 0.60],
                    "ease": "linear",
                },
            ),
        ),
    )
    captured_filters: list[str] = []

    monkeypatch.setattr(
        video_renderer.auto_reframe,
        "compute_crop_path",
        lambda *args, **kwargs: [(0, 0, 1080, 1920)],
    )
    monkeypatch.setattr(video_renderer.auto_reframe, "write_sendcmd_file", lambda *args: None)
    monkeypatch.setattr(
        video_renderer.auto_reframe,
        "build_filter_chain",
        lambda *args, **kwargs: "EXPLICIT_TRACKING_CHAIN",
    )
    monkeypatch.setattr(
        video_renderer,
        "_smart_camera_filter",
        lambda *args, **kwargs: "SMART_CAMERA_CHAIN",
    )

    def fake_run(cmd: list[str], *, timeout_s: float, stage: str) -> None:
        captured_filters.append(cmd[cmd.index("-vf") + 1])
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"")

    monkeypatch.setattr(video_renderer, "_run", fake_run)

    _paths, reframed = video_renderer.cut_segments(
        plan,
        asset_paths={1: src},
        intermediate_dir=tmp_path / "out",
        target_aspect="9:16",
        tracking_by_asset={1: {"frames": []}},
        tracking_target_by_asset={1: 0},
        smart_camera_enabled=True,
    )

    assert reframed == [True]
    assert captured_filters == ["SMART_CAMERA_CHAIN"]


def test_stabilize_segment_uses_stable_vidstab_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "seg_0000.mp4"
    src.write_bytes(b"fake")
    dst = tmp_path / "seg_0000.stab.mp4"
    filters: list[str] = []

    def fake_run(cmd: list[str], *, timeout_s: float, stage: str) -> None:
        if "-vf" in cmd:
            filters.append(cmd[cmd.index("-vf") + 1])
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        if cmd[-1] != "-":
            Path(cmd[-1]).write_bytes(b"")

    monkeypatch.setattr(video_renderer, "_run", fake_run)

    video_renderer._stabilize_segment(src, dst, tmp_path / "scratch")

    assert any("vidstabdetect=" in f and "shakiness=8" in f and "accuracy=9" in f for f in filters)
    assert any(
        "vidstabtransform=" in f and "smoothing=10" in f and "zoom=0" in f and "optzoom" not in f
        for f in filters
    )


def test_circlecrop_in_transition_whitelist() -> None:
    """Phase 8.1 — emotion-shift transitions resolve to circlecrop, not the default."""
    assert "circlecrop" in video_renderer.VALID_TRANSITIONS
    assert video_renderer._safe_transition("circlecrop") == "circlecrop"


# ----- v0.18 watermark / overlay -----


def test_watermark_position_xy_known_anchors() -> None:
    """Each of the nine anchor names maps to the expected ffmpeg expr."""
    assert video_renderer._watermark_position_xy("top-left") == ("${m}", "${m}")
    assert video_renderer._watermark_position_xy("top-right") == (
        "W-w-${m}",
        "${m}",
    )
    assert video_renderer._watermark_position_xy("middle-center") == (
        "(W-w)/2",
        "(H-h)/2",
    )
    assert video_renderer._watermark_position_xy("bottom-left") == (
        "${m}",
        "H-h-${m}",
    )
    assert video_renderer._watermark_position_xy("bottom-right") == (
        "W-w-${m}",
        "H-h-${m}",
    )


def test_watermark_position_xy_falls_back_to_default() -> None:
    """Unrecognised anchor names land at the documented default — never crash."""
    fallback = video_renderer._watermark_position_xy("nonsense")
    default = video_renderer._watermark_position_xy(video_renderer.WATERMARK_DEFAULT_POSITION)
    assert fallback == default


def test_watermark_filter_clamps_scale_and_opacity() -> None:
    """A degenerate row can't request a 200 % wide logo or 9.0 alpha."""
    f = video_renderer._watermark_filter(
        canvas_w=1080,
        canvas_h=1920,
        position="bottom-right",
        scale=2.0,  # over the cap
        opacity=9.0,  # over the cap
    )
    # Scale should be capped at WATERMARK_SCALE_MAX (0.5) → 540 px.
    assert "scale=540:-1" in f
    # Opacity should be capped at 1.0.
    assert "aa=1.0000" in f
    # Margin is 2% of canvas width (1080 * 0.02 = 21.6 → 22), stamped
    # onto the position expression.
    assert "overlay=W-w-22:H-h-22" in f


def test_watermark_filter_uses_real_value_when_in_range() -> None:
    f = video_renderer._watermark_filter(
        canvas_w=1080,
        canvas_h=1920,
        position="top-left",
        scale=0.10,
        opacity=0.5,
    )
    assert "scale=108:-1" in f
    assert "aa=0.5000" in f
    assert "overlay=22:22" in f


def test_apply_watermark_writes_output_in_fake_mode(tmp_path: Path) -> None:
    """End-to-end fake: ffmpeg path is stubbed; the helper must still produce
    an output file at the requested path so downstream stages can carry on."""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    wm = tmp_path / "logo.png"
    wm.write_bytes(b"\x89PNG fake")
    out = tmp_path / "out.mp4"
    video_renderer.apply_watermark(
        src,
        out,
        watermark_path=wm,
        target_aspect="9:16",
        position="bottom-right",
        scale=0.10,
        opacity=1.0,
    )
    assert out.is_file()


def test_apply_watermark_rejects_unknown_aspect(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    wm = tmp_path / "logo.png"
    wm.write_bytes(b"\x89PNG fake")
    with pytest.raises(video_renderer.VideoRenderError):
        video_renderer.apply_watermark(
            src,
            tmp_path / "out.mp4",
            watermark_path=wm,
            target_aspect="21:9",
        )


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
