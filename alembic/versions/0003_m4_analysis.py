"""M4: asset_transcripts, script_coverage, plus assets.analysis_steps_json + status check.

Revision ID: 0003_m4_analysis
Revises: 0002_m3_uploads
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_m4_analysis"
down_revision: str | None = "0002_m3_uploads"
branch_labels: str | None = None
depends_on: str | None = None


ASSET_STATUS = ("pending", "analyzing", "analyzed", "analysis_failed")


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # assets.analysis_steps_json — per-step bookkeeping; null until pipeline starts.
    op.add_column(
        "assets",
        sa.Column("analysis_steps_json", sa.JSON(), nullable=True),
    )
    # Tighten the assets.status accepted set; pre-M4 rows are all 'pending' so
    # the constraint applies cleanly without data fixup.
    op.create_check_constraint(
        "ck_assets_status",
        "assets",
        "status IN " + _in_clause(ASSET_STATUS),
    )

    # asset_transcripts — 1:1 with assets, holds zh-Hant SRT-style segments.
    op.create_table(
        "asset_transcripts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(length=16), nullable=False, server_default="zh-Hant"),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("transcript_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("segments_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "edited",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("asset_id", name="uq_asset_transcripts_asset_id"),
    )
    op.create_index(
        "ix_asset_transcripts_asset_id",
        "asset_transcripts",
        ["asset_id"],
    )

    # script_coverage — 1:1 per Asset, replaced on script edit or force re-run.
    op.create_table(
        "script_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "script_id",
            sa.Integer(),
            sa.ForeignKey("scripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("scripted_segment_count", sa.Integer(), nullable=False),
        sa.Column("total_segment_count", sa.Integer(), nullable=False),
        sa.Column("coverage_ratio_by_count", sa.Float(), nullable=False),
        sa.Column("coverage_ratio_by_duration_ms", sa.Float(), nullable=False),
        sa.Column("match_details_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("asset_id", name="uq_script_coverage_asset_id"),
    )
    op.create_index(
        "ix_script_coverage_asset_id",
        "script_coverage",
        ["asset_id"],
    )
    op.create_index(
        "ix_script_coverage_script_id",
        "script_coverage",
        ["script_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_script_coverage_script_id", table_name="script_coverage")
    op.drop_index("ix_script_coverage_asset_id", table_name="script_coverage")
    op.drop_table("script_coverage")
    op.drop_index("ix_asset_transcripts_asset_id", table_name="asset_transcripts")
    op.drop_table("asset_transcripts")
    op.drop_constraint("ck_assets_status", "assets", type_="check")
    op.drop_column("assets", "analysis_steps_json")
