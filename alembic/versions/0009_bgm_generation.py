"""v0.15 — bgm_generation_jobs table for AI music gen status / history.

Revision ID: 0009_bgm_generation
Revises: 0008_subtitle_cue
Create Date: 2026-05-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_bgm_generation"
down_revision: str | None = "0008_subtitle_cue"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "bgm_generation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Status flow: pending → running → done | failed:{reason}
        # ``failed`` rows keep the prompt + error so the UI can show
        # "上次失敗：…" without a separate log lookup.
        sa.Column("status", sa.String(64), nullable=False, server_default="pending"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("output_path", sa.String(1024), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("rq_job_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_bgm_generation_jobs_project_id",
        "bgm_generation_jobs",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_bgm_generation_jobs_project_id",
        table_name="bgm_generation_jobs",
    )
    op.drop_table("bgm_generation_jobs")
