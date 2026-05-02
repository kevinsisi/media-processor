"""M6.4 — projects.bgm_path for the optional background-music mix.

Revision ID: 0007_project_bgm
Revises: 0006_draft_comments
Create Date: 2026-05-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_project_bgm"
down_revision: str | None = "0006_draft_comments"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("bgm_path", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "bgm_path")
