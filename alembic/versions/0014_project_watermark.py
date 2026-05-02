"""v0.18 — projects.watermark_* columns for the brand-logo overlay.

Four columns describe an optional PNG watermark burned into the final
render: where the file lives on disk, plus position / scale / opacity
metadata. The settings columns are NOT NULL with sensible defaults so
existing rows pick up bottom-right / 10 % / fully opaque automatically;
``watermark_path`` is nullable because the feature is opt-in.

Revision ID: 0014_project_watermark
Revises: 0013_draft_segment_volume
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_project_watermark"
down_revision: str | None = "0013_draft_segment_volume"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("watermark_path", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "watermark_position",
            sa.String(length=16),
            nullable=False,
            server_default="bottom-right",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "watermark_scale",
            sa.Float(),
            nullable=False,
            server_default="0.10",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "watermark_opacity",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "watermark_opacity")
    op.drop_column("projects", "watermark_scale")
    op.drop_column("projects", "watermark_position")
    op.drop_column("projects", "watermark_path")
