"""RQ job entry point for NarratoAI documentary frame analysis.

Runs in worker-analysis queue (alongside Whisper / Gemini Vision).
Each job extracts keyframes from an asset video and calls Gemini Vision
in batches, then persists the result to Asset.frame_analysis_json.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def analyse_asset_frames(
    asset_id: int,
    *,
    interval_s: float = 3.0,
    force: bool = False,
) -> dict[str, Any]:
    """RQ job — run frame analysis pipeline for a single Asset.

    Returns a summary dict for the RQ job result store.
    """
    return asyncio.run(_run(asset_id, interval_s=interval_s, force=force))


async def _run(asset_id: int, *, interval_s: float, force: bool) -> dict[str, Any]:
    from media_processor.core.db import async_session_maker
    from media_processor.models import Asset
    from media_processor.services import frame_analysis_service
    from media_processor.services.settings_store import build_opencode_config, get_llm_api_keys

    async with async_session_maker() as session:
        asset = await session.get(Asset, asset_id)
        if asset is None:
            raise RuntimeError(f"asset {asset_id} not found")

        status = getattr(asset, "frame_analysis_status", "not_started")
        if status == "done" and not force:
            logger.info("asset %d: frame_analysis already done, skipping", asset_id)
            return {"asset_id": asset_id, "status": "skipped", "reason": "already done"}

        # Mark pending
        asset.frame_analysis_status = "pending"
        asset.frame_analysis_error = None
        await session.commit()

    try:
        async with async_session_maker() as session:
            asset = await session.get(Asset, asset_id)
            if asset is None:
                raise RuntimeError(f"asset {asset_id} not found after status update")

            api_keys = await get_llm_api_keys(session)
            opencode_config = await build_opencode_config(session)
            if not api_keys and opencode_config is None:
                raise RuntimeError("no Vision AI provider configured for frame analysis")

            asset.frame_analysis_status = "running"
            await session.commit()

        result = await frame_analysis_service.analyse_asset(
            str(asset.file_path),
            api_keys=tuple(api_keys),
            opencode_config=opencode_config,
            interval_s=interval_s,
        )

        async with async_session_maker() as session:
            asset = await session.get(Asset, asset_id)
            if asset is not None:
                asset.frame_analysis_json = result
                asset.frame_analysis_status = "done"
                asset.frame_analysis_error = None
                await session.commit()

        logger.info(
            "asset %d: frame analysis done — %d batches, %d frames",
            asset_id,
            result.get("batch_count", 0),
            result.get("frame_count", 0),
        )
        return {
            "asset_id": asset_id,
            "status": "done",
            "frame_count": result.get("frame_count", 0),
            "batch_count": result.get("batch_count", 0),
        }

    except Exception as exc:  # noqa: BLE001
        logger.exception("frame analysis failed for asset %d", asset_id)
        async with async_session_maker() as session:
            asset = await session.get(Asset, asset_id)
            if asset is not None:
                asset.frame_analysis_status = "failed"
                asset.frame_analysis_error = str(exc)[:1000]
                await session.commit()
        raise
