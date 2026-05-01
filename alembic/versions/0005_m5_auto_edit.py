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
    # pre-creating AssetSegment rows. Wrap in batch mode so SQLite (unit
    # tests) can rebuild the table for the alter/FK/check operations.
    with op.batch_alter_table("draft_segments") as batch_op:
        batch_op.alter_column(
            "asset_segment_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.add_column(sa.Column("asset_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("asset_start_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("asset_end_ms", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("source_kind", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("plan_reason", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_draft_segments_asset_id",
            "assets",
            ["asset_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_draft_segments_asset_range",
            "asset_start_ms IS NULL OR asset_end_ms IS NULL OR asset_start_ms < asset_end_ms",
        )
    op.create_index(
        "ix_draft_segments_asset_id",
        "draft_segments",
        ["asset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_draft_segments_asset_id", table_name="draft_segments")
    with op.batch_alter_table("draft_segments") as batch_op:
        batch_op.drop_constraint("ck_draft_segments_asset_range", type_="check")
        batch_op.drop_constraint("fk_draft_segments_asset_id", type_="foreignkey")
        batch_op.drop_column("plan_reason")
        batch_op.drop_column("source_kind")
        batch_op.drop_column("asset_end_ms")
        batch_op.drop_column("asset_start_ms")
        batch_op.drop_column("asset_id")
        batch_op.alter_column(
            "asset_segment_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    op.drop_column("drafts", "cut_plan_json")
    op.drop_column("drafts", "subtitle_path")
    op.drop_column("drafts", "progress_steps_json")
