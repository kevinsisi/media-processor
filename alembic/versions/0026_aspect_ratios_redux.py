"""v0.29.0 — aspect-ratio redux + crop-region anchor.

Three things in one migration so the rewrite, column add, and check
constraint flip happen atomically:

1. Rewrite legacy ``target_aspect_ratio`` rows from ``4:5`` / ``1:1``
   to ``9:16``. Operators stopped shipping IG-feed posts a year ago
   and the only surviving deliverables are 9:16 Reels + (new in
   v0.29) 16:9 landscape; the rewrite avoids leaving rows in a state
   the new ``Literal["9:16","16:9"]`` will reject at load time.

2. Drop the existing ``ck_projects_target_aspect_ratio`` check
   constraint and re-create it against the new 2-value tuple. Done
   AFTER the rewrite so we don't trip the constraint mid-update.

3. Add ``projects.crop_region_json`` (nullable JSON). Stores the
   static-crop anchor (``{x_norm, y_norm}``) used when source
   orientation differs from target orientation. ``NULL`` means
   centre — no cost to rows that pre-date this column.

Already-rendered draft mp4 / SRT files at the old aspect remain on
disk untouched; they continue to play. The next render on a
migrated project comes out 9:16. Already-emitted exports under
``v{N}-4x5-*.mp4`` / ``v{N}-1x1-*.mp4`` are NOT deleted — they
remain downloadable through the artifacts list.

Revision ID: 0026_aspect_ratios_redux
Revises: 0025_draft_exports
Create Date: 2026-05-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_aspect_ratios_redux"
down_revision: str | None = "0025_draft_exports"
branch_labels: str | None = None
depends_on: str | None = None


_CHECK_NAME = "ck_projects_target_aspect_ratio"


def _is_sqlite() -> bool:
    """SQLite needs ``batch_alter_table`` for any ALTER (incl. drop /
    re-add CHECK) — it has no native ALTER CONSTRAINT. The unit test
    suite uses SQLite via aiosqlite so the same migration must work
    there. Production runs Postgres where the direct path is fine
    too, but batch mode is harmless on Postgres so we use it
    unconditionally for consistency."""
    bind = op.get_bind()
    return bind.dialect.name == "sqlite"


def upgrade() -> None:
    # Step 1 — migrate legacy values BEFORE the new constraint takes
    # effect. Use op.execute so this works on every dialect; the
    # rewrite is a single UPDATE with a small IN-list so even a
    # production-sized projects table is sub-second.
    op.execute(
        sa.text(
            "UPDATE projects SET target_aspect_ratio = '9:16' "
            "WHERE target_aspect_ratio IN ('4:5', '1:1')"
        )
    )

    # Step 2 — swap the check constraint and add crop_region_json.
    # SQLite needs batch mode (rewrites the table) for the constraint
    # change; Postgres can do both inline. Use batch mode on SQLite
    # only so the Postgres path stays a cheap ALTER.
    if _is_sqlite():
        with op.batch_alter_table("projects") as batch:
            # SQLite re-creates the table on batch exit, so the
            # drop + re-add of the CHECK is effectively atomic.
            try:
                batch.drop_constraint(_CHECK_NAME, type_="check")
            except (KeyError, ValueError):
                # Some legacy dev DBs were bootstrapped without this
                # named constraint; re-creating below is what matters.
                pass
            batch.create_check_constraint(
                _CHECK_NAME,
                "target_aspect_ratio IN ('9:16', '16:9')",
            )
            batch.add_column(sa.Column("crop_region_json", sa.JSON(), nullable=True))
    else:
        try:
            op.drop_constraint(_CHECK_NAME, "projects", type_="check")
        except Exception:  # noqa: BLE001 — see comment above
            pass
        op.create_check_constraint(
            _CHECK_NAME,
            "projects",
            "target_aspect_ratio IN ('9:16', '16:9')",
        )
        op.add_column(
            "projects",
            sa.Column("crop_region_json", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("projects") as batch:
            batch.drop_column("crop_region_json")
            try:
                batch.drop_constraint(_CHECK_NAME, type_="check")
            except (KeyError, ValueError):
                pass
            batch.create_check_constraint(
                _CHECK_NAME,
                "target_aspect_ratio IN ('9:16', '4:5', '1:1')",
            )
    else:
        op.drop_column("projects", "crop_region_json")
        try:
            op.drop_constraint(_CHECK_NAME, "projects", type_="check")
        except Exception:  # noqa: BLE001
            pass
        op.create_check_constraint(
            _CHECK_NAME,
            "projects",
            "target_aspect_ratio IN ('9:16', '4:5', '1:1')",
        )
    # We deliberately do NOT reverse the 4:5/1:1 → 9:16 rewrite —
    # there's no way to know which rewritten rows were originally
    # which, and the operator already accepted that 4:5/1:1 are
    # gone when running upgrade.
