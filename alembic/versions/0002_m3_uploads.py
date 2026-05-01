"""M3: target_aspect_ratio on projects, plus scripts + upload_sessions tables.

Revision ID: 0002_m3_uploads
Revises: 0001_init
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_m3_uploads"
down_revision: str | None = "0001_init"
branch_labels: str | None = None
depends_on: str | None = None


TARGET_ASPECT_RATIO = ("9:16", "4:5", "1:1")
UPLOAD_KIND = ("video", "script")
UPLOAD_STATUS = ("pending", "complete", "aborted")


def _in_clause(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # Extend projects with target aspect ratio.
    op.add_column(
        "projects",
        sa.Column(
            "target_aspect_ratio",
            sa.String(length=8),
            nullable=False,
            server_default="9:16",
        ),
    )
    op.create_check_constraint(
        "ck_projects_target_aspect_ratio",
        "projects",
        "target_aspect_ratio IN " + _in_clause(TARGET_ASPECT_RATIO),
    )

    # scripts: one per project.
    op.create_table(
        "scripts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_filename", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", name="uq_scripts_project_id"),
    )

    # upload_sessions: chunked upload progress, persistent across reloads.
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("total_size", sa.Integer(), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("received_chunks", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN " + _in_clause(UPLOAD_KIND),
            name="ck_upload_sessions_kind",
        ),
        sa.CheckConstraint(
            "status IN " + _in_clause(UPLOAD_STATUS),
            name="ck_upload_sessions_status",
        ),
        sa.CheckConstraint("total_size >= 0", name="ck_upload_sessions_total_size"),
        sa.CheckConstraint("chunk_size > 0", name="ck_upload_sessions_chunk_size"),
    )
    op.create_index(
        "ix_upload_sessions_project_id",
        "upload_sessions",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_upload_sessions_project_id", table_name="upload_sessions")
    op.drop_table("upload_sessions")
    op.drop_table("scripts")
    op.drop_constraint("ck_projects_target_aspect_ratio", "projects", type_="check")
    op.drop_column("projects", "target_aspect_ratio")
