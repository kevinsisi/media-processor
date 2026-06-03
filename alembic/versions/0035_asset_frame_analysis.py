"""NarratoAI documentary integration — add frame analysis fields to assets.

Revision ID: 0035_asset_frame_analysis
Revises: 0034_story_narration_assets
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_asset_frame_analysis"
down_revision: str | None = "0034_story_narration_assets"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.add_column(sa.Column("frame_analysis_json", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column(
                "frame_analysis_status",
                sa.String(16),
                nullable=False,
                server_default="not_started",
            )
        )
        batch.add_column(sa.Column("frame_analysis_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("frame_analysis_error")
        batch.drop_column("frame_analysis_status")
        batch.drop_column("frame_analysis_json")
