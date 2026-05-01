"""M5: drafts gain progress/subtitle/cut-plan columns; draft_segments gain
direct asset_id + asset range columns (asset_segment_id becomes nullable).

Revision ID: 0005_m5_auto_edit
Revises: 0004_app_settings
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_m5_auto_edit"
down_revision: str | None = "0004_app_settings"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Drafts: M5 progress + subtitle path + cut-plan blob.
    op.add_column(
        "drafts",
        sa.Column("progress_steps_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "drafts",
        sa.Column("subtitle_path", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "drafts",
        sa.Column("cut_plan_json", sa.JSON(), nullable=True),
    )

    # DraftSegments: relax the asset_segment_id requirement and add direct
    # asset references so M5's Gemini planner can persist plans without
    # pre-creating AssetSegment rows.
    op.alter_column(
        "draft_segments",
        "asset_segment_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.add_column(
        "draft_segments",
        sa.Column("asset_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "draft_segments",
        sa.Column("asset_start_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "draft_segments",
        sa.Column("asset_end_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "draft_segments",
        sa.Column("source_kind", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "draft_segments",
        sa.Column("plan_reason", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_draft_segments_asset_id",
        "draft_segments",
        "assets",
        ["asset_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_draft_segments_asset_id",
        "draft_segments",
        ["asset_id"],
    )
    op.create_check_constraint(
        "ck_draft_segments_asset_range",
        "draft_segments",
        "asset_start_ms IS NULL OR asset_end_ms IS NULL "
        "OR asset_start_ms < asset_end_ms",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_draft_segments_asset_range", "draft_segments", type_="check"
    )
    op.drop_index("ix_draft_segments_asset_id", table_name="draft_segments")
    op.drop_constraint(
        "fk_draft_segments_asset_id", "draft_segments", type_="foreignkey"
    )
    op.drop_column("draft_segments", "plan_reason")
    op.drop_column("draft_segments", "source_kind")
    op.drop_column("draft_segments", "asset_end_ms")
    op.drop_column("draft_segments", "asset_start_ms")
    op.drop_column("draft_segments", "asset_id")
    op.alter_column(
        "draft_segments",
        "asset_segment_id",
        existing_type=sa.Integer(),
        nullable=False,
    )

    op.drop_column("drafts", "cut_plan_json")
    op.drop_column("drafts", "subtitle_path")
    op.drop_column("drafts", "progress_steps_json")
