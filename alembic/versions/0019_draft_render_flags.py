"""v0.21.1 — drafts.render_flags_json snapshot for skip-plan re-renders.

Adds a single nullable JSON column ``drafts.render_flags_json`` so the
two skip-plan re-render endpoints (timeline reorder, subtitle re-burn)
can read back the user's original transitions / stabilize / subtitles /
auto_reframe choices instead of silently defaulting every flag to
``True``. ``None`` keeps the historical "use the all-True defaults"
behaviour for legacy rows.

Revision ID: 0019_draft_render_flags
Revises: 0018_project_subject_class
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_draft_render_flags"
down_revision: str | None = "0018_project_subject_class"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "drafts",
        sa.Column("render_flags_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("drafts", "render_flags_json")
