"""Add StoryScript artifacts for Narrato-style story mode.

Revision ID: 0033_story_scripts
Revises: 0032_upload_total_size_bigint
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_story_scripts"
down_revision: str | None = "0032_upload_total_size_bigint"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "story_scripts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("drafts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ready"),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("script_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_story_scripts_project_id", "story_scripts", ["project_id"])
    op.create_index("ix_story_scripts_draft_id", "story_scripts", ["draft_id"])


def downgrade() -> None:
    op.drop_index("ix_story_scripts_draft_id", table_name="story_scripts")
    op.drop_index("ix_story_scripts_project_id", table_name="story_scripts")
    op.drop_table("story_scripts")
