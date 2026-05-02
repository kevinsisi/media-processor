"""M7.2 — subtitle_cue table for the inline subtitle editor.

Revision ID: 0008_subtitle_cue
Revises: 0007_project_bgm
Create Date: 2026-05-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_subtitle_cue"
down_revision: str | None = "0007_project_bgm"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "subtitle_cues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "draft_id",
            sa.Integer(),
            sa.ForeignKey("drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("draft_id", "idx", name="uq_subtitle_cues_draft_idx"),
        sa.CheckConstraint("start_ms < end_ms", name="ck_subtitle_cues_range"),
    )
    op.create_index(
        "ix_subtitle_cues_draft_id",
        "subtitle_cues",
        ["draft_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_subtitle_cues_draft_id", table_name="subtitle_cues")
    op.drop_table("subtitle_cues")
