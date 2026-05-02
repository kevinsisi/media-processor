"""v0.17 — assets.tracked_object_index + custom_roi_json columns.

Lets the user pick which tracked object the renderer's auto-reframe
stage should follow (or supply a custom ROI run through OpenCV CSRT).

Revision ID: 0012_asset_tracking_target
Revises: 0011_draft_bgm
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_asset_tracking_target"
down_revision: str | None = "0011_draft_bgm"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("tracked_object_index", sa.Integer(), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("custom_roi_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "custom_roi_json")
    op.drop_column("assets", "tracked_object_index")
