"""v0.21.6 — watermark_presets table for user-saved logo overlays.

A new global resource (not project-scoped): operators save their
current project's watermark configuration as a named preset, then
apply it to other projects without re-uploading the PNG. Each preset
owns its own file under ``${WATERMARK_DIR}/_presets/{preset_id}.png``
so the lifecycle is independent of any project — when a project is
deleted its applied watermark file is removed but the preset is
untouched, and vice versa.

Revision ID: 0020_watermark_presets
Revises: 0019_draft_render_flags
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_watermark_presets"
down_revision: str | None = "0019_draft_render_flags"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "watermark_presets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column(
            "position",
            sa.String(length=16),
            nullable=False,
            server_default="bottom-right",
        ),
        sa.Column(
            "scale",
            sa.Float(),
            nullable=False,
            server_default="0.10",
        ),
        sa.Column(
            "opacity",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("watermark_presets")
