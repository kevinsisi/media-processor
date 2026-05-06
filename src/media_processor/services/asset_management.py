"""v0.26.0 — single + batch asset deletion. v0.27.1 — force-delete.

v0.26 refused to delete an asset whose ``DraftSegment.asset_id`` was
still referenced by an active Draft (status in pending / processing /
ready_for_review / approved). The user had to reject those drafts
manually first, which made bulk cleanup tedious.

v0.27.1 flips this: by default the deletion still aborts when an
active draft references the asset, but instead of raising
``AssetInUseError`` we return a structured ``AssetDeleteResult`` with
``deleted=False`` plus the list of ``affected_drafts`` so the FE can
warn the user. When the caller passes ``force=True`` we DO delete:

  * Wipe every ``DraftSegment`` row whose ``(draft_id, asset_id)``
    pair points at this asset. This satisfies the
    ``DraftSegment.asset_id ondelete=RESTRICT`` FK directly — no need
    to delete the parent draft just to free the asset.
  * For each affected draft, recount its remaining segments. If it
    has zero segments left, flip its ``status`` to ``failed`` and
    write ``prompt_feedback = "素材已被刪除"`` so the operator sees
    why the version died next time they open ProjectEdit. We do NOT
    delete the draft row itself in that case — the message would be
    lost and the operator wouldn't know what happened.
  * Failed / rejected drafts that already reference the asset are
    cascade-deleted as before (the v0.26 path is unchanged for them
    — they have no useful state to preserve). The ``_drop_dead_drafts``
    query joins on ``DraftSegment.asset_id == asset_id``, which after
    our segment wipe no longer matches the freshly-marked-failed
    drafts; their rows survive.
  * Disk + DB row cleanup proceeds as in v0.26.

Single-asset deletion is the building block; the batch endpoint
calls it per-asset so a partial-failure surfaces per-row in the
response (one bad asset doesn't block the rest of the batch).
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, func, select
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


# Drafts in any of these statuses block a non-force asset deletion.
# The user is either still working with them (pending / processing /
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


# Sentinel string written to ``Draft.prompt_feedback`` when a force-
# delete invalidates the draft (i.e. the asset wipe leaves the draft
# with zero segments). Surfaced verbatim in the FE.
DRAFT_INVALIDATED_REASON = "素材已被刪除"


@dataclass(frozen=True)
class BlockingDraft:
    """One active-draft reference to an asset.

    Returned in :class:`AssetDeleteResult.affected_drafts` so the FE
    can render the version list in its confirm dialog.
    """

    draft_id: int
    version: int
    status: str


@dataclass
class AssetDeleteResult:
    """Outcome of a single :func:`delete_asset` call.

    ``deleted`` is the canonical success flag.

    ``affected_drafts`` is non-empty in two scenarios:
      * ``force=False`` and at least one active draft referenced the
        asset → ``deleted=False``, asset row + disk untouched. The FE
        prompts the user to confirm and retries with ``force=True``.
      * ``force=True`` regardless of whether any blockers existed →
        the same list, retained for the response so the FE knows
        which draft versions just got invalidated.

    ``invalidated_versions`` is the subset of affected-draft versions
    whose segments were all wiped (the draft was flipped to ``failed``
    with ``prompt_feedback = "素材已被刪除"``). Always empty when
    ``force=False``.

    ``not_found`` is set when the asset row didn't exist; ``deleted``
    stays ``False`` and ``affected_drafts`` is empty. Surfaces a 404
    on the single endpoint and a "not found" reason in batch.
    """

    asset_id: int
    deleted: bool
    affected_drafts: list[BlockingDraft] = field(default_factory=list)
    invalidated_versions: list[int] = field(default_factory=list)
    not_found: bool = False
    # Populated only when an unexpected exception is caught during a
    # batch delete. Single-asset deletion lets exceptions propagate;
    # batch swallows them per-row so one bad row doesn't kill the rest.
    error_message: str | None = None


# Kept around for backwards-compat with anyone that still imports it
# (no longer raised by the v0.27.1 paths). Tests + external callers
# should switch to :class:`AssetDeleteResult`.
class AssetDeleteError(RuntimeError):
    """Base for asset-deletion failures."""


class AssetNotFoundError(AssetDeleteError):
    """No row with that id."""


class AssetInUseError(AssetDeleteError):
    """Legacy v0.26 error — no longer raised by :func:`delete_asset`.

    Kept for import-compat. New callers should inspect the
    :class:`AssetDeleteResult` return value instead.
    """

    def __init__(self, asset_id: int, blocking_draft_versions: list[int]):
        self.asset_id = asset_id
        self.blocking_draft_versions = blocking_draft_versions
        super().__init__(
            f"asset {asset_id} is referenced by active drafts "
            f"v{','.join(str(v) for v in blocking_draft_versions)}"
        )


async def _blocking_drafts_for(
    session: AsyncSession, asset_id: int
) -> list[BlockingDraft]:
    """Drafts in a blocking status that reference this asset.

    We pull only ``Draft.id`` / ``version`` / ``status`` (scalar
    tuple) rather than full ``Draft`` rows. Selecting full rows +
    ``.distinct()`` fails on PostgreSQL because the Draft model has
    JSON columns and ``json`` has no built-in equality operator —
    DISTINCT can't deduplicate without one. Tuple-of-scalars side-
    steps the issue and the response only needs the version int +
    status string for the FE warning anyway.
    """
    stmt = (
        select(Draft.id, Draft.version, Draft.status)
        .join(DraftSegment, DraftSegment.draft_id == Draft.id)
        .where(
            DraftSegment.asset_id == asset_id,
            Draft.status.in_(BLOCKING_DRAFT_STATUSES),
        )
        .distinct()
    )
    rows = (await session.execute(stmt)).all()
    seen: dict[int, BlockingDraft] = {}
    for did, version, status_val in rows:
        seen[int(did)] = BlockingDraft(
            draft_id=int(did),
            version=int(version),
            status=str(status_val),
        )
    return sorted(seen.values(), key=lambda b: b.version)


async def _force_invalidate_drafts(
    session: AsyncSession,
    asset_id: int,
    blockers: list[BlockingDraft],
) -> list[int]:
    """Wipe DraftSegments of each blocking draft that reference this
    asset, and flip drafts that lose all their segments to ``failed``
    with ``prompt_feedback = DRAFT_INVALIDATED_REASON``.

    Returns the sorted list of newly-invalidated draft versions so
    the caller can echo it in :class:`AssetDeleteResult`.

    The remaining segments (those pointing at OTHER assets) are left
    alone — this is intentional. A half-wired draft is broken state
    the operator chose by force-deleting; we keep the row so the
    feedback message persists and the operator can hit re-render or
    delete it explicitly.
    """
    invalidated: list[int] = []
    for b in blockers:
        await session.execute(
            delete(DraftSegment).where(
                DraftSegment.draft_id == b.draft_id,
                DraftSegment.asset_id == asset_id,
            )
        )
        # Flush so the count below sees the deletion.
        await session.flush()

        remaining = (
            await session.execute(
                select(func.count())
                .select_from(DraftSegment)
                .where(DraftSegment.draft_id == b.draft_id)
            )
        ).scalar_one()
        if remaining == 0:
            draft = await session.get(Draft, b.draft_id)
            if draft is None:  # pragma: no cover — concurrent delete.
                continue
            draft.status = DraftStatus.FAILED.value
            draft.prompt_feedback = DRAFT_INVALIDATED_REASON
            invalidated.append(b.version)
            logger.info(
                "asset %d: draft v%d (id=%d) lost its last segment to force-delete; flipped to failed",
                asset_id,
                b.version,
                b.draft_id,
            )
        else:
            logger.info(
                "asset %d: wiped its segments from draft v%d (id=%d); %d segments remain",
                asset_id,
                b.version,
                b.draft_id,
                int(remaining),
            )
    return sorted(invalidated)


async def _drop_dead_drafts_referencing(
    session: AsyncSession, asset_id: int
) -> int:
    """Cascade-delete every failed / rejected draft that still uses
    this asset, returning the count.

    The cascade is configured on ``Project.drafts`` (and on
    ``Draft.segments``), so ``await session.delete(draft)`` walks
    DraftSegment + DraftComment + Review rows for free. We delete
    them one-at-a-time so SQLAlchemy can run the relationship
    cascades; a single ``DELETE`` statement bypasses ORM hooks and
    leaves orphan segments behind.

    Two-step query: first collect distinct draft ids (cheap; no
    JSON-comparison issue), then re-fetch the full Draft rows by
    id so the ORM cascade fires on ``session.delete``.

    NOTE: this query joins on ``DraftSegment.asset_id == asset_id``,
    so drafts whose segments referencing this asset have already
    been wiped (e.g. in :func:`_force_invalidate_drafts`) are
    excluded — exactly what we want, because we just marked them
    failed and don't want to delete the row.
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


async def delete_asset(
    session: AsyncSession,
    asset_id: int,
    *,
    force: bool = False,
) -> AssetDeleteResult:
    """Delete one asset.

    Returns an :class:`AssetDeleteResult`. The caller commits — this
    function flushes / deletes within ``session`` but doesn't
    ``commit``, so a Redis / disk error elsewhere in the request can
    roll the transaction back cleanly.

    Behaviour matrix:

    * Asset row missing → ``deleted=False, not_found=True``.
    * No active drafts reference the asset → delete it (asset row +
      disk + AssetTranscript / ScriptCoverage + cascade-delete
      failed / rejected drafts that referenced it). Returns
      ``deleted=True`` with empty ``affected_drafts``.
    * Active drafts reference and ``force=False`` → ``deleted=False``
      with ``affected_drafts`` populated. Asset is NOT touched.
    * Active drafts reference and ``force=True`` → wipe their
      segments referencing this asset; drafts that lose their last
      segment flip to ``failed`` with ``prompt_feedback`` and stay;
      then proceed with the normal delete. Returns ``deleted=True``
      with ``affected_drafts`` + ``invalidated_versions`` populated.
    """
    asset = await session.get(Asset, asset_id)
    if asset is None:
        return AssetDeleteResult(
            asset_id=asset_id, deleted=False, not_found=True
        )

    blockers = await _blocking_drafts_for(session, asset_id)

    if blockers and not force:
        return AssetDeleteResult(
            asset_id=asset_id,
            deleted=False,
            affected_drafts=blockers,
        )

    invalidated_versions: list[int] = []
    if blockers:  # force=True path
        invalidated_versions = await _force_invalidate_drafts(
            session, asset_id, blockers
        )

    # Cascade-delete failed / rejected drafts that still reference
    # this asset (i.e. whose DraftSegment.asset_id row points at it).
    # After our force-invalidate pass above the just-failed drafts
    # have no such segments anymore, so they're correctly excluded
    # from this cleanup and survive with their feedback message.
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

    return AssetDeleteResult(
        asset_id=asset_id,
        deleted=True,
        affected_drafts=blockers,
        invalidated_versions=invalidated_versions,
    )


async def batch_delete_assets(
    session: AsyncSession,
    asset_ids: Sequence[int],
    *,
    force: bool = False,
) -> dict[int, AssetDeleteResult]:
    """Run :func:`delete_asset` over each id; collect per-row results.

    Returns a ``{asset_id: AssetDeleteResult}`` map. The caller is
    responsible for ``await session.commit()`` after inspecting the
    result — partial failures still commit because each successful
    delete should land regardless of the others.

    The ``force`` flag is threaded through to every per-asset call,
    so a batch with ``force=False`` returns "blocked" rows the FE
    can confirm + retry with ``force=True``.
    """
    out: dict[int, AssetDeleteResult] = {}
    for asset_id in asset_ids:
        try:
            out[asset_id] = await delete_asset(session, asset_id, force=force)
        except Exception as exc:  # noqa: BLE001 — surface to the caller.
            logger.exception("asset %d: unexpected delete failure", asset_id)
            out[asset_id] = AssetDeleteResult(
                asset_id=asset_id,
                deleted=False,
                error_message=f"internal error: {type(exc).__name__}",
            )
    return out


__all__ = [
    "BLOCKING_DRAFT_STATUSES",
    "DRAFT_INVALIDATED_REASON",
    "AssetDeleteError",
    "AssetDeleteResult",
    "AssetInUseError",
    "AssetNotFoundError",
    "BlockingDraft",
    "batch_delete_assets",
    "delete_asset",
]
