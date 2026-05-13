"""RQ job entry point for v0.40.0 asset-level stabilization."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def stabilize_asset(asset_id: int, *, force: bool = False) -> dict[str, Any]:
    logger.info("stabilize_asset: asset_id=%d force=%s", asset_id, force)
    from media_processor.services.asset_stabilization_runner import run_asset_stabilization

    return asyncio.run(run_asset_stabilization(asset_id, force=force))


__all__ = ["stabilize_asset"]
