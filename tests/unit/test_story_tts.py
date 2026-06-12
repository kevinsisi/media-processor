from __future__ import annotations

from pathlib import Path

from media_processor.models import StoryNarrationAsset
from media_processor.services import story_tts
from media_processor.services.edit_planner import CutPlan, CutPlanSegment


def test_word_events_to_srt_groups_words_into_paced_cues() -> None:
    events = [
        {"offset": 0, "duration": 1_000_000, "text": "這"},
        {"offset": 1_000_000, "duration": 1_000_000, "text": "不是"},
        {"offset": 2_000_000, "duration": 1_000_000, "text": "一般"},
    ]

    srt = story_tts._word_events_to_srt(events)

    assert "00:00:00,000 --> 00:00:00,320" in srt
    assert "這不是一般" in srt


def test_word_events_to_srt_preserves_latin_word_spaces() -> None:
    events = [
        {"offset": 0, "duration": 1_000_000, "text": "The"},
        {"offset": 1_000_000, "duration": 1_000_000, "text": "BMW"},
        {"offset": 2_000_000, "duration": 1_000_000, "text": "M4"},
        {"offset": 3_000_000, "duration": 1_000_000, "text": "很快"},
    ]

    srt = story_tts._word_events_to_srt(events)

    assert "The BMW M4很快" in srt
    assert "TheBMWM4" not in srt


def test_narration_subtitles_from_plan_reads_audio_sidecar(tmp_path: Path) -> None:
    audio_path = tmp_path / "narration.m4a"
    audio_path.write_bytes(b"audio")
    audio_path.with_suffix(".srt").write_text(
        "1\n00:00:00,000 --> 00:00:00,500\n字幕\n",
        encoding="utf-8",
    )
    plan = CutPlan(
        schema_version="story.cut-plan.v1",
        target_duration_ms=500,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(
                1,
                10,
                0,
                500,
                "scripted",
                "narration",
                narration_audio_path=str(audio_path),
            ),
        ),
    )

    assert story_tts.narration_subtitles_from_plan(plan) == {
        1: "1\n00:00:00,000 --> 00:00:00,500\n字幕"
    }


def test_edge_artifact_without_sidecar_is_not_reused(tmp_path: Path) -> None:
    audio_path = tmp_path / "narration.m4a"
    audio_path.write_bytes(b"audio")
    row = StoryNarrationAsset(
        project_id=1,
        story_script_id=1,
        story_item_order=1,
        asset_id=1,
        source_start_ms=0,
        source_end_ms=1000,
        narration_text_hash="hash",
        provider="edge",
        voice="zh-TW-HsiaoChenNeural",
        status=story_tts.NARRATION_STATUS_DONE,
        file_path=str(audio_path),
        duration_ms=1000,
    )

    assert story_tts._artifact_is_reusable(row) is False
