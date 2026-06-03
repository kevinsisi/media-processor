"""Unit tests for narration_script_generator (NarratoAI documentary / drama_explain)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from media_processor.services import narration_script_generator as nsg
from media_processor.services.story_script import StoryScriptInputError


def _make_asset(
    project_id: int = 1,
    asset_id: int = 10,
    duration_ms: int = 60_000,
    fa_json=None,
    fa_status="done",
):
    asset = MagicMock()
    asset.id = asset_id
    asset.project_id = project_id
    asset.duration_ms = duration_ms
    asset.frame_analysis_json = fa_json
    asset.frame_analysis_status = fa_status
    return asset


def _minimal_fa_json(interval_s: float = 3.0) -> dict:
    return {
        "interval_seconds": interval_s,
        "frame_count": 6,
        "batch_count": 1,
        "batches": [
            {
                "batch_index": 0,
                "time_range": "00:00:00,000-00:00:18,000",
                "frame_observations": [
                    {"timestamp": "00:00:00,000", "observation": "開場畫面"},
                    {"timestamp": "00:00:03,000", "observation": "主角出現"},
                ],
                "overall_activity_summary": "主角登場行走",
            }
        ],
    }


def _valid_story_script_json(asset_id: int = 10) -> str:
    import json

    return json.dumps(
        {
            "schema_version": "story-script.v1",
            "title": "測試短影音",
            "summary": "測試摘要",
            "items": [
                {
                    "order": 1,
                    "asset_id": asset_id,
                    "source_start_ms": 0,
                    "source_end_ms": 10000,
                    "picture": "開場畫面",
                    "narration": "這是解說旁白",
                    "audio_intent": "narration",
                    "beat_type": "hook",
                    "reason": "鉤子段落",
                }
            ],
        }
    )


class TestGenerateDocumentaryScript:
    @pytest.mark.asyncio
    async def test_raises_when_no_frame_analysis(self):
        session = MagicMock()
        asset = _make_asset(fa_json=None, fa_status="not_started")
        with pytest.raises(StoryScriptInputError, match="frame_analysis_json"):
            await nsg.generate_documentary_script(session, asset, project_name="test")

    @pytest.mark.asyncio
    async def test_raises_when_empty_batches(self):
        session = MagicMock()
        asset = _make_asset(fa_json={"interval_seconds": 3.0, "batches": []})
        with pytest.raises(StoryScriptInputError, match="frame_analysis_json"):
            await nsg.generate_documentary_script(session, asset, project_name="test")

    @pytest.mark.asyncio
    async def test_success_path_uses_llm_output(self):
        session = MagicMock()
        asset = _make_asset(fa_json=_minimal_fa_json())

        with patch(
            "media_processor.services.narration_script_generator._call_llm",
            new=AsyncMock(return_value=_valid_story_script_json(asset_id=10)),
        ):
            doc = await nsg.generate_documentary_script(session, asset, project_name="TestProject")

        assert doc.project_id == 1
        assert doc.title == "測試短影音"
        assert len(doc.items) == 1
        assert doc.metadata.get("mode") == "documentary"
        assert doc.metadata.get("used_fallback") is False

    @pytest.mark.asyncio
    async def test_fallback_when_llm_returns_none(self):
        session = MagicMock()
        asset = _make_asset(fa_json=_minimal_fa_json())

        with patch(
            "media_processor.services.narration_script_generator._call_llm",
            new=AsyncMock(return_value=None),
        ):
            doc = await nsg.generate_documentary_script(session, asset, project_name="TestProject")

        # Falls back to heuristic document
        assert doc.project_id == 1
        assert doc.metadata.get("used_fallback") is True

    @pytest.mark.asyncio
    async def test_fallback_when_llm_returns_invalid_json(self):
        session = MagicMock()
        asset = _make_asset(fa_json=_minimal_fa_json())

        with patch(
            "media_processor.services.narration_script_generator._call_llm",
            new=AsyncMock(return_value="this is not json at all!!!"),
        ):
            doc = await nsg.generate_documentary_script(session, asset, project_name="TestProject")

        assert doc.metadata.get("used_fallback") is True

    @pytest.mark.asyncio
    async def test_brief_injected_into_prompt(self):
        """Verify project_brief is woven into the generated prompt."""
        session = MagicMock()
        asset = _make_asset(fa_json=_minimal_fa_json())
        captured_prompt = []

        async def mock_llm(prompt, _session):
            captured_prompt.append(prompt)

        with patch(
            "media_processor.services.narration_script_generator._call_llm",
            side_effect=mock_llm,
        ):
            await nsg.generate_documentary_script(
                session, asset, project_name="TestProject", project_brief="汽車廣告風格"
            )

        assert captured_prompt, "LLM was not called"
        assert "汽車廣告風格" in captured_prompt[0]


class TestParseTsMs:
    def test_zero(self):
        assert nsg._parse_ts_ms("00:00:00,000") == 0

    def test_one_second(self):
        assert nsg._parse_ts_ms("00:00:01,000") == 1000

    def test_period_separator(self):
        assert nsg._parse_ts_ms("00:00:01.500") == 1500

    def test_invalid_returns_zero(self):
        assert nsg._parse_ts_ms("not-a-timestamp") == 0
