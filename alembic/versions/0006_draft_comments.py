"""M5.2 — per-version draft comments thread.

Revision ID: 0006_draft_comments
Revises: 0005_m5_auto_edit
Create Date: 2026-05-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_draft_comments"
down_revision: str | None = "0005_m5_auto_edit"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ``sa.Column(..., index=True)`` inside ``op.create_table`` already
    # emits ``CREATE INDEX ix_draft_comments_draft_id``, so don't follow up
    # with an explicit ``op.create_index`` — that would re-create the same
    # index name and fail with DuplicateTable.
    op.create_table(
        "draft_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "draft_id",
            sa.Integer(),
            sa.ForeignKey("drafts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("author", sa.String(length=64), nullable=False, server_default="anonymous"),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("draft_comments")
