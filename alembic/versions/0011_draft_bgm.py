"""v0.16.2 — drafts.bgm_path snapshot column.

Each draft now records which BGM file it was rendered with (a snapshot of
``Project.bgm_path`` taken at first render). Re-renders of the same draft
keep that path even if the user generates a new AI BGM on the project, so
older drafts don't silently swap their soundtrack.

Revision ID: 0011_draft_bgm
Revises: 0010_asset_tracking
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_draft_bgm"
down_revision: str | None = "0010_asset_tracking"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "drafts",
        sa.Column("bgm_path", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drafts", "bgm_path")
