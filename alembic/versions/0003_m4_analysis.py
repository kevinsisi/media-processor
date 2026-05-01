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
    # Add analysis_steps_json + tighten assets.status check set in one batch so
    # SQLite (unit tests) can rebuild the table without ALTER-constraint support.
    # Pre-M4 rows are all 'pending', so the new constraint applies without fixup.
    with op.batch_alter_table("assets") as batch_op:
        batch_op.add_column(sa.Column("analysis_steps_json", sa.JSON(), nullable=True))
        batch_op.create_check_constraint(
            "ck_assets_status",
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
    with op.batch_alter_table("assets") as batch_op:
        batch_op.drop_constraint("ck_assets_status", type_="check")
        batch_op.drop_column("analysis_steps_json")
