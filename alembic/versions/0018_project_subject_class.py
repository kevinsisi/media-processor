"""v0.21 — project-level subject class for auto-trim.

Adds ``projects.subject_class`` (str, nullable). ``NULL`` means "不限"
(no subject filter — legacy behaviour). When set, the edit planner
shrinks each chosen segment's ``[asset_start_ms, asset_end_ms)`` to the
subject's appearance range (±500ms tolerance) and demotes assets that
don't contain the class to last-resort priority. The valid value space
is the COCO-80 class names from
``services.object_tracking.COCO80_CLASSES``; the API validates against
that list before writing.

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
