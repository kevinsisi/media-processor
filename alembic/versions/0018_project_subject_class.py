"""v0.21 — projects.subject_class for the auto-edit subject filter.

Adds a single nullable ``subject_class`` column on ``projects`` so the
edit planner can clamp each asset's used span to the time range where
the chosen COCO-80 class appears in ``tracking_json``. ``None`` keeps
the historical behaviour (full-duration eligible).

Revision ID: 0018_project_subject_class
Revises: 0017_secondary_subtitles
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_project_subject_class"
down_revision: str | None = "0017_secondary_subtitles"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("subject_class", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "subject_class")
