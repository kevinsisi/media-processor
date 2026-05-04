"""v0.26.0 — single + batch asset deletion.

Deletes the on-disk artefacts (source mp4, generated thumbnails) AND
the DB row. Refuses with ``AssetInUseError`` when at least one
``Draft`` whose status is NOT in ``BLOCKING_DRAFT_STATUSES`` (i.e.
the user hasn't yet rejected / failed it) still references the
asset via ``DraftSegment.asset_id``. The user has to reject those
drafts first or wait for them to fail; allowing the delete would
break the cascade rule on ``DraftSegment.asset_id`` (FK
``ondelete="RESTRICT"``).

Failed / rejected drafts are tolerated: their segment rows are
cascade-deleted along with the draft row, freeing up the asset for
the FK to release. The endpoint deletes those drafts first, then
the asset.

Single-asset deletion is the building block; the batch endpoint
calls it per-asset so a partial-failure surfaces per-row in the
response (one bad asset doesn't block the rest of the batch).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.models import (
    Asset,
    AssetTranscript,
    Draft,
    DraftSegment,
    DraftStatus,
    ScriptCoverage,
)
from media_processor.services import thumbnails as thumbnails_svc

logger = logging.getLogger(__name__)


# Drafts in any of these statuses block asset deletion. The user is
# either still working with them (pending / processing /
# ready_for_review / approved) or has otherwise opted to keep the
# row active. ``failed`` and ``rejected`` are explicitly NOT in this
# set — we cascade-delete those drafts (and their segments) inline
# so a long history of rejected experiments doesn't pin every asset
# in place.
BLOCKING_DRAFT_STATUSES: frozenset[str] = frozenset(
    {
        DraftStatus.PENDING.value,
        DraftStatus.PROCESSING.value,
        DraftStatus.READY_FOR_REVIEW.value,
        DraftStatus.APPROVED.value,
    }
)


class AssetDeleteError(RuntimeError):
    """Base for asset-deletion failures."""


class AssetNotFoundError(AssetDeleteError):
    """No row with that id."""


class AssetInUseError(AssetDeleteError):
    """An active draft still references this asset.

    ``blocking_draft_versions`` is the list of ``Draft.version`` ints
    the FE can render in the error message ("v3, v5 都還在用這支
    素材；先在 ProjectEdit 裡刪掉它們").
    """

    def __init__(self, asset_id: int, blocking_draft_versions: list[int]):
        self.asset_id = asset_id
        self.blocking_draft_versions = blocking_draft_versions
        super().__init__(
            f"asset {asset_id} is referenced by active drafts "
            f"v{','.join(str(v) for v in blocking_draft_versions)}"
        )


async def _blocking_draft_versions_for(
    session: AsyncSession, asset_id: int
) -> list[int]:
    """Versions of drafts in a blocking status that reference this asset.

    We pull only ``Draft.version`` (and ``Draft.id`` to dedupe) rather
    than full ``Draft`` rows. Selecting full rows + ``.distinct()``
    fails on PostgreSQL because the Draft model has JSON columns and
    ``json`` has no built-in equality operator — DISTINCT can't
    deduplicate without one. Tuple-of-scalars side-steps the issue
    and we only need the version int for the error message anyway.
    """
    stmt = (
        select(Draft.id, Draft.version)
        .join(DraftSegment, DraftSegment.draft_id == Draft.id)
        .where(
            DraftSegment.asset_id == asset_id,
            Draft.status.in_(BLOCKING_DRAFT_STATUSES),
        )
        .distinct()
    )
    rows = (await session.execute(stmt)).all()
    return sorted({int(r[1]) for r in rows})


async def _drop_dead_drafts_referencing(
    session: AsyncSession, asset_id: int
) -> int:
    """Cascade-delete every failed / rejected draft that uses this
    asset, returning the count.

    The cascade is configured on ``Project.drafts`` (and on
    ``Draft.segments``), so ``await session.delete(draft)`` walks
    DraftSegment + DraftComment + Review rows for free. We delete
    them one-at-a-time so SQLAlchemy can run the relationship
    cascades; a single ``DELETE`` statement bypasses ORM hooks and
    leaves orphan segments behind.

    Two-step query: first collect distinct draft ids (cheap; no
    JSON-comparison issue), then re-fetch the full Draft rows by
    id so the ORM cascade fires on ``session.delete``.
    """
    id_rows = (
        await session.execute(
            select(Draft.id)
            .join(DraftSegment, DraftSegment.draft_id == Draft.id)
            .where(
                DraftSegment.asset_id == asset_id,
                Draft.status.in_(
                    (
                        DraftStatus.FAILED.value,
                        DraftStatus.REJECTED.value,
                    )
                ),
            )
            .distinct()
        )
    ).scalars().all()
    if not id_rows:
        return 0
    drafts = (
        (
            await session.execute(
                select(Draft).where(Draft.id.in_(id_rows))
            )
        ).scalars().all()
    )
    for d in drafts:
        await session.delete(d)
    return len(drafts)


def _delete_on_disk(asset: Asset) -> None:
    """Remove the source file + thumbnails directory.

    Best-effort — a missing file is fine (user might have already
    cleaned the disk manually). Permission / IO failures are logged
    and swallowed because the DB row is the canonical record; an
    orphan file is a smaller leak than an orphan row.
    """
    src = Path(asset.file_path) if asset.file_path else None
    if src is not None and src.is_file():
        try:
            src.unlink()
        except OSError as exc:
            logger.warning(
                "asset %d: failed to unlink source %s: %s",
                asset.id,
                src,
                exc,
            )
    thumb_dir = thumbnails_svc.asset_thumb_dir(settings.thumbnails_dir, asset.id)
    if thumb_dir.is_dir():
        try:
            shutil.rmtree(thumb_dir)
        except OSError as exc:
            logger.warning(
                "asset %d: failed to remove thumbnails dir %s: %s",
                asset.id,
                thumb_dir,
                exc,
            )


async def delete_asset(session: AsyncSession, asset_id: int) -> None:
    """Delete one asset.

    Raises:
      * ``AssetNotFoundError`` when no row exists.
      * ``AssetInUseError`` when at least one Draft in a blocking
        status references the asset.

    The DB transaction is the caller's — this function flushes /
    deletes within ``session`` but doesn't ``commit``. The caller
    commits after either the single-asset or the batch finishes so a
    Redis / disk error rolls the row deletion back cleanly.
    """
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFoundError(f"asset {asset_id} not found")

    blocking_versions = await _blocking_draft_versions_for(session, asset_id)
    if blocking_versions:
        raise AssetInUseError(asset_id, blocking_versions)

    # Cascade-delete failed / rejected drafts so the
    # DraftSegment.asset_id RESTRICT FK doesn't block the asset row.
    dropped = await _drop_dead_drafts_referencing(session, asset_id)
    if dropped:
        logger.info(
            "asset %d: cascade-deleted %d failed/rejected draft(s) before asset removal",
            asset_id,
            dropped,
        )

    # Delete dependent rows that have no relationship cascade onto
    # Asset (legacy single-table coverage / transcript / etc. that
    # were never wired up to Asset's relationship tree).
    await session.execute(
        delete(AssetTranscript).where(AssetTranscript.asset_id == asset_id)
    )
    await session.execute(
        delete(ScriptCoverage).where(ScriptCoverage.asset_id == asset_id)
    )

    # Disk cleanup BEFORE the DB delete so a disk failure leaves the
    # row in place and the user can retry. Once the row is gone we
    # have no record of the file path.
    _delete_on_disk(asset)

    await session.delete(asset)


async def batch_delete_assets(
    session: AsyncSession,
    asset_ids: Sequence[int],
) -> dict[int, str | None]:
    """Run :func:`delete_asset` over each id; collect per-row results.

    Returns a ``{asset_id: error_message_or_None}`` map. ``None``
    means the asset was deleted successfully; a string is the human-
    readable reason it stayed.

    The caller is responsible for ``await session.commit()`` after
    inspecting the result — partial failures still commit because
    each successful delete should land regardless of the others.
    """
    out: dict[int, str | None] = {}
    for asset_id in asset_ids:
        try:
            await delete_asset(session, asset_id)
        except AssetNotFoundError:
            out[asset_id] = "not found"
        except AssetInUseError as exc:
            versions = ", ".join(f"v{v}" for v in exc.blocking_draft_versions)
            out[asset_id] = f"still used by active draft(s): {versions}"
        except Exception as exc:  # noqa: BLE001 — surface to the caller.
            logger.exception("asset %d: unexpected delete failure", asset_id)
            out[asset_id] = f"internal error: {type(exc).__name__}"
        else:
            out[asset_id] = None
    return out


__all__ = [
    "BLOCKING_DRAFT_STATUSES",
    "AssetDeleteError",
    "AssetInUseError",
    "AssetNotFoundError",
    "batch_delete_assets",
    "delete_asset",
]
