"""Generate keyframe-gallery thumbnails for any Asset that doesn't have them.

Runs against the live database. Skips assets whose 5-frame set is already
complete on disk so it's safe to re-run after partial failures.

Usage:
    docker compose exec api python scripts/backfill_thumbnails.py
    # or for a dry-run plan only:
    docker compose exec api python scripts/backfill_thumbnails.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select

from media_processor.api.config import settings
from media_processor.core.db import async_session_maker
from media_processor.models import Asset
from media_processor.services import thumbnails as thumbnails_svc
from media_processor.services import uploads as upload_svc


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _list_assets() -> list[Asset]:
    async with async_session_maker() as session:
        rows = (
            (await session.execute(select(Asset).order_by(Asset.id.asc())))
            .scalars()
            .all()
        )
        return list(rows)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="don't run ffmpeg, just plan")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-generate even when 5 frames already exist",
    )
    args = parser.parse_args(argv)

    _setup_logging()
    log = logging.getLogger("backfill_thumbnails")

    thumb_root = settings.thumbnails_dir
    Path(thumb_root).mkdir(parents=True, exist_ok=True)

    assets = await _list_assets()
    log.info("found %d asset rows; thumbnails_dir=%s", len(assets), thumb_root)

    planned: list[Asset] = []
    skipped_complete = 0
    skipped_missing_file = 0
    for asset in assets:
        if (
            not args.force
            and thumbnails_svc.has_complete_set(thumb_root, asset.id)
        ):
            skipped_complete += 1
            continue
        if not Path(asset.file_path).is_file():
            skipped_missing_file += 1
            log.warning("skip asset %d — source file missing: %s", asset.id, asset.file_path)
            continue
        planned.append(asset)

    log.info(
        "skipped: %d already-complete, %d source-missing — planned: %d",
        skipped_complete,
        skipped_missing_file,
        len(planned),
    )
    if args.dry_run:
        for asset in planned:
            log.info("would generate for asset %d (%s)", asset.id, asset.file_path)
        return 0

    succeeded = 0
    failed = 0
    for asset in planned:
        # Older deployments may have stored duration_ms=0 because ffprobe was
        # missing from the api container (now fixed). Re-probe at backfill
        # time so the keyframe seeks land at sensible offsets, and persist
        # the corrected value back to the DB while we're at it.
        duration_ms = asset.duration_ms
        if duration_ms <= 0:
            probe = await asyncio.to_thread(upload_svc.probe_media, asset.file_path)
            if probe.duration_ms > 0:
                duration_ms = probe.duration_ms
                async with async_session_maker() as session:
                    db_asset = await session.get(Asset, asset.id)
                    if db_asset is not None:
                        db_asset.duration_ms = duration_ms
                        if not db_asset.resolution and probe.resolution:
                            db_asset.resolution = probe.resolution
                        if not db_asset.fps and probe.fps:
                            db_asset.fps = probe.fps
                        if not db_asset.codec and probe.codec:
                            db_asset.codec = probe.codec
                        await session.commit()
                log.info(
                    "asset %d: reprobed duration_ms=%d (was 0); persisted",
                    asset.id,
                    duration_ms,
                )
            else:
                log.warning(
                    "asset %d: ffprobe returned 0 duration — skipping (file may be unreadable)",
                    asset.id,
                )
                failed += 1
                continue
        try:
            result = await asyncio.to_thread(
                thumbnails_svc.generate,
                asset.id,
                asset.file_path,
                duration_ms,
                thumb_root,
                force=args.force,
            )
        except Exception:  # noqa: BLE001
            log.exception("asset %d: generate raised", asset.id)
            failed += 1
            continue
        if result.failed_reason and result.frames_written == 0:
            log.error(
                "asset %d: failed (%s) — written=%d skipped=%d",
                asset.id,
                result.failed_reason,
                result.frames_written,
                result.frames_skipped,
            )
            failed += 1
        else:
            log.info(
                "asset %d: ok — written=%d skipped=%d failure=%s",
                asset.id,
                result.frames_written,
                result.frames_skipped,
                result.failed_reason,
            )
            succeeded += 1

    log.info("done — succeeded=%d failed=%d", succeeded, failed)
    return 0 if succeeded > 0 or len(planned) == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
