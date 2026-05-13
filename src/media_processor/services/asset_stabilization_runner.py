"""Worker-side runner for v0.40.0 asset-level stabilization."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from media_processor.api.config import settings
from media_processor.core.db import async_session_maker
from media_processor.models import Asset
from media_processor.services import asset_variants

logger = logging.getLogger(__name__)


async def run_asset_stabilization(asset_id: int, *, force: bool = False) -> dict[str, str | int]:
    async with async_session_maker() as session:
        asset = (
            await session.execute(select(Asset).where(Asset.id == asset_id))
        ).scalar_one_or_none()
        if asset is None:
            logger.warning("run_asset_stabilization: asset %d not found", asset_id)
            return {"asset_id": asset_id, "status": "missing"}
        if (
            not force
            and asset_variants.stabilization_status(asset) == asset_variants.STABILIZATION_DONE
            and asset.stabilized_path
            and Path(asset.stabilized_path).is_file()
        ):
            return {"asset_id": asset_id, "status": "done"}
        asset.stabilization_status = asset_variants.STABILIZATION_RUNNING
        asset.stabilization_error = None
        dst = asset_variants.stabilized_path_for_asset(asset)
        asset.stabilized_path = str(dst)
        await session.commit()
        src = Path(asset.file_path)
        scratch = Path(settings.analysis_dir) / "asset_stabilization" / str(asset_id)

    try:
        await asyncio.to_thread(asset_variants.stabilize_source, src, dst, scratch)
    except Exception as exc:  # noqa: BLE001 — persist terminal state for the UI.
        logger.exception("run_asset_stabilization: asset %d failed", asset_id)
        async with async_session_maker() as session:
            row = await session.get(Asset, asset_id)
            if row is not None:
                row.stabilization_status = asset_variants.STABILIZATION_FAILED
                row.stabilization_error = f"{type(exc).__name__}: {exc}"
                await session.commit()
        return {"asset_id": asset_id, "status": "failed"}

    async with async_session_maker() as session:
        row = await session.get(Asset, asset_id)
        if row is None:
            return {"asset_id": asset_id, "status": "missing_after"}
        row.stabilized_path = str(dst)
        row.stabilization_status = asset_variants.STABILIZATION_DONE
        row.stabilization_error = None
        await session.commit()
    return {"asset_id": asset_id, "status": "done"}


__all__ = ["run_asset_stabilization"]
