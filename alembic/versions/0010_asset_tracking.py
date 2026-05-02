"""v0.16 — assets.tracking_json column for YOLO per-frame bbox data.

Revision ID: 0010_asset_tracking
Revises: 0009_bgm_generation
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_asset_tracking"
down_revision: str | None = "0009_bgm_generation"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("tracking_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "tracking_json")
