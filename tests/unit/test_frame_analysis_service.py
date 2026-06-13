"""Unit tests for frame_analysis_service (NarratoAI documentary pipeline)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from media_processor.services import frame_analysis_service as fas
from media_processor.services.opencode_client import OpenCodeConfig


class TestAnalysisToMarkdown:
    def test_empty_batches(self):
        md = fas.analysis_to_markdown({"batches": []})
        assert md == ""

    def test_single_batch_structure(self):
        fa = {
            "batches": [
                {
                    "batch_index": 0,
                    "time_range": "00:00:00,000-00:00:30,000",
                    "frame_observations": [
                        {"timestamp": "00:00:00,000", "observation": "開場畫面"},
                        {"timestamp": "00:00:03,000", "observation": "主角出現"},
                    ],
                    "overall_activity_summary": "主角在街上行走",
                }
            ]
        }
        md = fas.analysis_to_markdown(fa)
        assert "00:00:00,000-00:00:30,000" in md
        assert "主角在街上行走" in md
        assert "開場畫面" in md
        assert "主角出現" in md

    def test_multiple_batches_sorted_by_index(self):
        fa = {
            "batches": [
                {
                    "batch_index": 1,
                    "time_range": "B",
                    "frame_observations": [],
                    "overall_activity_summary": "batch1",
                },
                {
                    "batch_index": 0,
                    "time_range": "A",
                    "frame_observations": [],
                    "overall_activity_summary": "batch0",
                },
            ]
        }
        md = fas.analysis_to_markdown(fa)
        # batch0 should appear before batch1
        assert md.index("batch0") < md.index("batch1")

    def test_missing_observation_fields_handled(self):
        fa = {
            "batches": [
                {
                    "batch_index": 0,
                    "time_range": "00:00:00,000-00:00:10,000",
                    "frame_observations": [{"timestamp": "", "observation": ""}],
                    "overall_activity_summary": "",
                }
            ]
        }
        # Should not raise
        md = fas.analysis_to_markdown(fa)
        assert isinstance(md, str)


class TestCacheKey:
    def test_deterministic(self):
        k1 = fas._cache_key("path/to/video.mp4", 1234567.0, 3.0)
        k2 = fas._cache_key("path/to/video.mp4", 1234567.0, 3.0)
        assert k1 == k2

    def test_different_interval_gives_different_key(self):
        k1 = fas._cache_key("path/to/video.mp4", 1234567.0, 3.0)
        k2 = fas._cache_key("path/to/video.mp4", 1234567.0, 5.0)
        assert k1 != k2

    def test_different_mtime_gives_different_key(self):
        k1 = fas._cache_key("path/to/video.mp4", 1234567.0, 3.0)
        k2 = fas._cache_key("path/to/video.mp4", 9999999.0, 3.0)
        assert k1 != k2

    def test_key_is_20_chars(self):
        k = fas._cache_key("video.mp4", 0.0, 3.0)
        assert len(k) == 20


class TestMsToSrtTime:
    def test_zero(self):
        assert fas._ms_to_srt_time(0) == "00:00:00,000"

    def test_one_second(self):
        assert fas._ms_to_srt_time(1000) == "00:00:01,000"

    def test_one_minute(self):
        assert fas._ms_to_srt_time(60_000) == "00:01:00,000"

    def test_one_hour(self):
        assert fas._ms_to_srt_time(3_600_000) == "01:00:00,000"

    def test_mixed(self):
        assert fas._ms_to_srt_time(3_723_456) == "01:02:03,456"


class TestAnalyseAssetNoKeys:
    @pytest.mark.asyncio
    async def test_raises_when_no_api_keys(self):
        with pytest.raises(RuntimeError, match="no Vision AI provider"):
            await fas.analyse_asset("/tmp/fake.mp4", api_keys=())

    @pytest.mark.asyncio
    async def test_accepts_opencode_without_gemini_keys(self):
        with (
            patch(
                "media_processor.services.frame_analysis_service.extract_keyframes",
                return_value=[MagicMock(spec=Path)],
            ),
            patch(
                "media_processor.services.frame_analysis_service._analyse_batch",
                new=AsyncMock(
                    return_value={
                        "batch_index": 0,
                        "time_range": "00:00:00,000-00:00:03,000",
                        "frame_observations": [],
                        "overall_activity_summary": "ok",
                    }
                ),
            ),
        ):
            result = await fas.analyse_asset(
                "/tmp/fake.mp4",
                api_keys=(),
                opencode_config=OpenCodeConfig(
                    servers=("http://opencode.local",),
                    model="openai/gpt-5.5",
                    variant="medium",
                    password="",
                ),
            )

        assert result["batch_count"] == 1


class TestExtractKeyframesCacheHit:
    def test_cache_hit_skips_ffmpeg(self, tmp_path):
        cache_dir = tmp_path / "frame_cache" / "abcde12345678901234"
        cache_dir.mkdir(parents=True)
        # Create fake cached frames
        for i in range(3):
            (cache_dir / f"frame_{i:05d}.jpg").write_bytes(b"fake")

        with (
            patch("media_processor.services.frame_analysis_service.settings") as mock_settings,
            patch("os.path.getmtime", return_value=1234.0),
            patch(
                "media_processor.services.frame_analysis_service._cache_key",
                return_value="abcde12345678901234",
            ),
        ):
            mock_settings.frame_cache_dir = str(tmp_path / "frame_cache")
            frames = fas.extract_keyframes("/fake/video.mp4", interval_s=3.0)

        assert len(frames) == 3


class TestAnalyseBatchFallback:
    @pytest.mark.asyncio
    async def test_opencode_used_before_gemini(self):
        fake_frames = [MagicMock(spec=Path)]
        for f in fake_frames:
            f.read_bytes.return_value = b"fake_jpeg"

        with (
            patch(
                "media_processor.services.frame_analysis_service._call_opencode_vision",
                new=AsyncMock(
                    return_value={
                        "frame_observations": [{"observation": "opencode"}],
                        "overall_activity_summary": "opencode summary",
                    }
                ),
            ) as oc_mock,
            patch(
                "media_processor.services.frame_analysis_service._call_gemini_vision",
                new=AsyncMock(return_value=None),
            ) as gemini_mock,
        ):
            result = await fas._analyse_batch(
                fake_frames,
                batch_index=0,
                start_ms=0,
                end_ms=3000,
                interval_s=3.0,
                api_keys=("key1",),
                opencode_config=OpenCodeConfig(
                    servers=("http://opencode.local",),
                    model="openai/gpt-5.5",
                    variant="medium",
                    password="",
                ),
            )

        assert result["overall_activity_summary"] == "opencode summary"
        oc_mock.assert_awaited_once()
        gemini_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_keys_fail_raises_without_synthetic_success(self):
        fake_frames = [MagicMock(spec=Path) for _ in range(2)]
        for f in fake_frames:
            f.read_bytes.return_value = b"fake_jpeg"

        with (
            patch(
                "media_processor.services.frame_analysis_service._call_gemini_vision",
                new=AsyncMock(return_value=None),
            ),
            pytest.raises(RuntimeError, match="all Vision providers failed"),
        ):
            await fas._analyse_batch(
                fake_frames,
                batch_index=0,
                start_ms=0,
                end_ms=6000,
                interval_s=3.0,
                api_keys=("key1", "key2"),
            )
