"""v0.28.1 — durable draft export artifacts.

Revision ID: 0025_draft_exports
Revises: 0024_asset_point_tracking_status
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_draft_exports"
down_revision: str | None = "0024_asset_point_tracking_status"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "draft_exports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("aspect", sa.String(length=8), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("output_filename", sa.String(length=255), nullable=False),
        sa.Column("output_path", sa.String(length=1024), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','done','failed')",
            name="ck_draft_exports_status",
        ),
        sa.CheckConstraint("height >= 480", name="ck_draft_exports_height_min"),
        sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_draft_exports_draft_id"), "draft_exports", ["draft_id"], unique=False)
    op.create_index(op.f("ix_draft_exports_job_id"), "draft_exports", ["job_id"], unique=False)
    op.create_index(op.f("ix_draft_exports_status"), "draft_exports", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_draft_exports_status"), table_name="draft_exports")
    op.drop_index(op.f("ix_draft_exports_job_id"), table_name="draft_exports")
    op.drop_index(op.f("ix_draft_exports_draft_id"), table_name="draft_exports")
    op.drop_table("draft_exports")
