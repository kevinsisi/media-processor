"""Add StoryScript narration audio artifacts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0034_story_narration_assets"
down_revision = "0033_story_scripts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story_narration_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("story_script_id", sa.Integer(), sa.ForeignKey("story_scripts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("story_item_order", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("source_start_ms", sa.Integer(), nullable=False),
        sa.Column("source_end_ms", sa.Integer(), nullable=False),
        sa.Column("narration_text_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("voice", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "story_script_id",
            "story_item_order",
            "narration_text_hash",
            "provider",
            "voice",
            name="uq_story_narration_identity",
        ),
    )
    op.create_index("ix_story_narration_assets_project_id", "story_narration_assets", ["project_id"])
    op.create_index("ix_story_narration_assets_draft_id", "story_narration_assets", ["draft_id"])
    op.create_index("ix_story_narration_assets_story_script_id", "story_narration_assets", ["story_script_id"])
    op.create_index("ix_story_narration_assets_narration_text_hash", "story_narration_assets", ["narration_text_hash"])
    op.create_index(
        "ix_story_narration_reuse",
        "story_narration_assets",
        ["project_id", "story_item_order", "narration_text_hash", "provider", "voice", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_story_narration_reuse", table_name="story_narration_assets")
    op.drop_index("ix_story_narration_assets_narration_text_hash", table_name="story_narration_assets")
    op.drop_index("ix_story_narration_assets_story_script_id", table_name="story_narration_assets")
    op.drop_index("ix_story_narration_assets_draft_id", table_name="story_narration_assets")
    op.drop_index("ix_story_narration_assets_project_id", table_name="story_narration_assets")
    op.drop_table("story_narration_assets")
