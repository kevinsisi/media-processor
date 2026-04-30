"""Initial schema: 9 entities for media-processor M2.

Revision ID: 0001_init
Revises:
Create Date: 2026-04-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_init"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


PROJECT_STATUS = ("pending", "processing", "degraded", "ready_for_review", "approved", "failed")
DRAFT_STATUS = (
    "pending",
    "processing",
    "ready_for_review",
    "approved",
    "rejected",
    "failed",
)
REVIEW_ACTION = ("approve", "reject", "repatch", "download")


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("client", sa.String(length=255), nullable=True),
        sa.Column("profile_name", sa.String(length=128), nullable=False),
        sa.Column("source_dir", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN " + _in_clause(PROJECT_STATUS),
            name="ck_projects_status",
        ),
    )
    op.create_index("ix_projects_name", "projects", ["name"])
    op.create_index("ix_projects_profile_name", "projects", ["profile_name"])

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("fps", sa.Float(), nullable=True),
        sa.Column("codec", sa.String(length=64), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("thumbnail_path", sa.String(length=1024), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
    )
    op.create_index("ix_assets_project_id", "assets", ["project_id"])
    op.create_index("ix_assets_sha256", "assets", ["sha256"])

    op.create_table(
        "asset_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tag_type", sa.String(length=64), nullable=False),
        sa.Column("tag_name", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("source_model", sa.String(length=64), nullable=False),
        sa.Column("time_ranges_ms", sa.JSON(), nullable=True),
        sa.UniqueConstraint(
            "asset_id",
            "tag_type",
            "tag_name",
            "source_model",
            name="uq_asset_tags_dedup",
        ),
    )
    op.create_index("ix_asset_tags_asset_id", "asset_tags", ["asset_id"])

    op.create_table(
        "asset_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("used_in_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("start_ms < end_ms", name="ck_asset_segments_range"),
    )
    op.create_index("ix_asset_segments_asset_id", "asset_segments", ["asset_id"])

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("output_zip_path", sa.String(length=1024), nullable=True),
        sa.Column("mp4_preview_path", sa.String(length=1024), nullable=True),
        sa.Column("ai_score", sa.Float(), nullable=True),
        sa.Column("prompt_feedback", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "version", name="uq_drafts_project_version"),
        sa.CheckConstraint("version >= 1", name="ck_drafts_version_positive"),
        sa.CheckConstraint(
            "status IN " + _in_clause(DRAFT_STATUS),
            name="ck_drafts_status",
        ),
    )
    op.create_index("ix_drafts_project_id", "drafts", ["project_id"])

    op.create_table(
        "draft_segments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "draft_id",
            sa.Integer(),
            sa.ForeignKey("drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column(
            "asset_segment_id",
            sa.Integer(),
            sa.ForeignKey("asset_segments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("on_timeline_start_ms", sa.Integer(), nullable=False),
        sa.Column("on_timeline_end_ms", sa.Integer(), nullable=False),
        sa.Column("reframe_keyframes", sa.JSON(), nullable=True),
        sa.Column("transition", sa.String(length=64), nullable=True),
        sa.Column("blurred_source_path", sa.String(length=1024), nullable=True),
        sa.UniqueConstraint("draft_id", "order", name="uq_draft_segments_order"),
        sa.CheckConstraint(
            "on_timeline_start_ms < on_timeline_end_ms",
            name="ck_draft_segments_range",
        ),
    )
    op.create_index("ix_draft_segments_draft_id", "draft_segments", ["draft_id"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "draft_id",
            sa.Integer(),
            sa.ForeignKey("drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reviewer", sa.String(length=64), nullable=False, server_default="alice"),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("prompt_feedback", sa.Text(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "action IN " + _in_clause(REVIEW_ACTION),
            name="ck_reviews_action",
        ),
    )
    op.create_index("ix_reviews_draft_id", "reviews", ["draft_id"])

    op.create_table(
        "bgms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("bpm", sa.Float(), nullable=True),
        sa.Column("beat_grid_json", sa.JSON(), nullable=True),
    )

    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("yaml_text", sa.Text(), nullable=False),
        sa.Column(
            "loaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_profiles_name"),
    )


def downgrade() -> None:
    # Reverse FK order: leaves first, roots last.
    op.drop_table("profiles")
    op.drop_table("bgms")
    op.drop_index("ix_reviews_draft_id", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("ix_draft_segments_draft_id", table_name="draft_segments")
    op.drop_table("draft_segments")
    op.drop_index("ix_drafts_project_id", table_name="drafts")
    op.drop_table("drafts")
    op.drop_index("ix_asset_segments_asset_id", table_name="asset_segments")
    op.drop_table("asset_segments")
    op.drop_index("ix_asset_tags_asset_id", table_name="asset_tags")
    op.drop_table("asset_tags")
    op.drop_index("ix_assets_sha256", table_name="assets")
    op.drop_index("ix_assets_project_id", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_projects_profile_name", table_name="projects")
    op.drop_index("ix_projects_name", table_name="projects")
    op.drop_table("projects")
