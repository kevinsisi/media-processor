"""v0.18 — projects.subtitle_* customisation columns.

Six string columns on ``projects`` so the user can pick the subtitle
font / colour / outline / position / size from the project edit page.
The renderer reads these to build the drawtext filter; defaults match
the pre-v0.18 burn-in look so untouched projects render identically.

NB: this migration is intentionally numbered 0015 to leave 0014 free
for an in-flight watermark feature on a sibling worktree.

Revision ID: 0015_project_subtitle_style
Revises: 0013_draft_segment_volume
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_project_subtitle_style"
down_revision: str | None = "0013_draft_segment_volume"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "subtitle_font",
            sa.String(length=64),
            nullable=False,
            server_default="noto_sans_tc",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "subtitle_color",
            sa.String(length=16),
            nullable=False,
            server_default="#ffffff",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "subtitle_outline_color",
            sa.String(length=16),
            nullable=False,
            server_default="#000000",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "subtitle_position",
            sa.String(length=16),
            nullable=False,
            server_default="bottom",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "subtitle_size",
            sa.String(length=16),
            nullable=False,
            server_default="medium",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "subtitle_outline_width",
            sa.String(length=16),
            nullable=False,
            server_default="thin",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "subtitle_outline_width")
    op.drop_column("projects", "subtitle_size")
    op.drop_column("projects", "subtitle_position")
    op.drop_column("projects", "subtitle_outline_color")
    op.drop_column("projects", "subtitle_color")
    op.drop_column("projects", "subtitle_font")
