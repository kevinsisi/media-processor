"""Unit tests for services.subtitles — SRT generation from a CutPlan."""

from __future__ import annotations

from media_processor.services.edit_planner import CutPlan, CutPlanSegment
from media_processor.services.subtitles import (
    MAX_LINE_CHARS,
    SubtitleCue,
    _format_timecode,
    _wrap_text,
    build_cues,
    build_srt,
    render_srt,
)


class _FakeTranscript:
    def __init__(self, segments: list[dict[str, object]]) -> None:
        self.segments_json = segments


def _plan(segments: list[CutPlanSegment]) -> CutPlan:
    return CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=10_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=tuple(segments),
        notes="",
    )


def test_format_timecode_basic() -> None:
    assert _format_timecode(0) == "00:00:00,000"
    assert _format_timecode(1_000) == "00:00:01,000"
    assert _format_timecode(3_661_500) == "01:01:01,500"


def test_format_timecode_clamps_negative() -> None:
    assert _format_timecode(-100) == "00:00:00,000"


def test_wrap_text_single_line() -> None:
    short = "你好世界"
    assert _wrap_text(short) == short


def test_wrap_text_two_lines() -> None:
    raw = "1234567890" * 4  # 40 chars
    out = _wrap_text(raw)
    lines = out.split("\n")
    assert len(lines) <= 2
    for line in lines:
        assert len(line) <= MAX_LINE_CHARS


def test_wrap_text_truncates_with_ellipsis() -> None:
    raw = "a" * 200
    out = _wrap_text(raw)
    lines = out.split("\n")
    assert len(lines) == 2
    assert lines[-1].endswith("…")


def test_build_cues_clips_into_cut() -> None:
    # Cut runs 1500..3500 of asset 7; transcript covers 1000..4000 with one line.
    cut = CutPlanSegment(
        order=0,
        asset_id=7,
        asset_start_ms=1_500,
        asset_end_ms=3_500,
        source_kind="improv",
        reason="",
    )
    plan = _plan([cut])
    transcript = _FakeTranscript(
        [{"idx": 0, "start_ms": 1_000, "end_ms": 4_000, "text": "中文字幕"}]
    )
    cues = build_cues(plan, {7: transcript})  # type: ignore[arg-type]
    assert len(cues) == 1
    cue = cues[0]
    # Timeline starts at 0; clipped local range [0, 2000] (the cut's full span).
    assert cue.timeline_start_ms == 0
    assert cue.timeline_end_ms == 2_000
    assert cue.text == "中文字幕"


def test_build_cues_two_adjacent_segments_in_one_cut() -> None:
    cut = CutPlanSegment(
        order=0,
        asset_id=1,
        asset_start_ms=0,
        asset_end_ms=4_000,
        source_kind="scripted",
        reason="",
    )
    plan = _plan([cut])
    transcript = _FakeTranscript(
        [
            {"idx": 0, "start_ms": 0, "end_ms": 1_500, "text": "第一句"},
            {"idx": 1, "start_ms": 1_500, "end_ms": 3_000, "text": "第二句"},
        ]
    )
    cues = build_cues(plan, {1: transcript})  # type: ignore[arg-type]
    assert [c.text for c in cues] == ["第一句", "第二句"]
    # Sequence numbers re-monotonic.
    assert [c.sequence for c in cues] == [1, 2]


def test_build_cues_no_overlap_returns_empty() -> None:
    cut = CutPlanSegment(
        order=0,
        asset_id=1,
        asset_start_ms=10_000,
        asset_end_ms=12_000,
        source_kind="improv",
        reason="",
    )
    plan = _plan([cut])
    transcript = _FakeTranscript([{"idx": 0, "start_ms": 0, "end_ms": 1_000, "text": "略"}])
    cues = build_cues(plan, {1: transcript})  # type: ignore[arg-type]
    assert cues == []


def test_build_cues_advances_timeline_across_cuts() -> None:
    cuts = [
        CutPlanSegment(0, 1, 0, 2_000, "improv", ""),
        CutPlanSegment(1, 2, 0, 3_000, "improv", ""),
    ]
    plan = _plan(cuts)
    transcripts = {
        1: _FakeTranscript([{"idx": 0, "start_ms": 0, "end_ms": 1_500, "text": "甲"}]),
        2: _FakeTranscript([{"idx": 0, "start_ms": 0, "end_ms": 2_000, "text": "乙"}]),
    }
    cues = build_cues(plan, transcripts)  # type: ignore[arg-type]
    # Cut 0 starts at 0; cut 1 starts at d_0 - TRANSITION_OVERLAP_MS because
    # adjacent cuts overlap on the rendered timeline (M6.3 xfade chain).
    from media_processor.services.subtitles import TRANSITION_OVERLAP_MS

    assert cues[0].timeline_start_ms == 0
    assert cues[1].timeline_start_ms == 2_000 - TRANSITION_OVERLAP_MS


def test_build_cues_xfade_overlap_three_cuts() -> None:
    """With N cuts, cut k starts at sum(d_i for i<k) - k*TRANSITION_OVERLAP_MS."""
    from media_processor.services.subtitles import TRANSITION_OVERLAP_MS

    cuts = [
        CutPlanSegment(0, 1, 0, 2_000, "improv", ""),
        CutPlanSegment(1, 2, 0, 2_500, "improv", ""),
        CutPlanSegment(2, 3, 0, 3_000, "improv", ""),
    ]
    plan = _plan(cuts)
    transcripts = {
        i: _FakeTranscript([{"idx": 0, "start_ms": 0, "end_ms": 800, "text": f"第{i}"}])
        for i in (1, 2, 3)
    }
    cues = build_cues(plan, transcripts)  # type: ignore[arg-type]
    assert len(cues) == 3
    assert cues[0].timeline_start_ms == 0
    assert cues[1].timeline_start_ms == 2_000 - TRANSITION_OVERLAP_MS
    assert cues[2].timeline_start_ms == 2_000 + 2_500 - 2 * TRANSITION_OVERLAP_MS


def test_build_cues_clamps_adjacent_overlap_after_xfade_offset() -> None:
    cuts = [
        CutPlanSegment(0, 1, 0, 5_700, "scripted", ""),
        CutPlanSegment(1, 2, 0, 2_800, "scripted", ""),
    ]
    plan = _plan(cuts)
    transcripts = {
        1: _FakeTranscript([{"idx": 0, "start_ms": 4_520, "end_ms": 5_700, "text": "前一句"}]),
        2: _FakeTranscript([{"idx": 0, "start_ms": 0, "end_ms": 2_800, "text": "後一句"}]),
    }

    cues = build_cues(plan, transcripts)  # type: ignore[arg-type]

    assert len(cues) == 2
    assert cues[0].timeline_end_ms == cues[1].timeline_start_ms
    assert cues[0].timeline_end_ms == 5_200


def test_render_srt_round_trip() -> None:
    cues = [
        SubtitleCue(1, 0, 1_500, "你好"),
        SubtitleCue(2, 1_500, 3_000, "世界"),
    ]
    text = render_srt(cues)
    # Two blocks separated by a blank line, trailing newline.
    assert text.count("-->") == 2
    assert "00:00:01,500 --> 00:00:03,000" in text
    assert text.endswith("\n")


def test_build_srt_empty_returns_empty_string() -> None:
    plan = _plan([])
    assert build_srt(plan, {}) == ""
