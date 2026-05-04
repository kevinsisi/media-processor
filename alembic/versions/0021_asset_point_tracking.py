"""v0.23.0 — assets.point_tracking_json + .point_tracking_origin for
pyramidal Lucas-Kanade pixel-precise tracking.

Two new nullable JSON columns. ``point_tracking_json`` holds the
LK-computed per-frame ``{t_ms, x, y, lost}`` trace consumed by
``services.auto_reframe`` when ``tracked_object_index == -4``.
``point_tracking_origin`` keeps a verbatim record of the user's
click (raw pixel + 0..1 normalised) so the FE can render the
crosshair on any thumbnail size and so the trace can be re-run later
without losing the operator's intent.

Both columns are nullable; existing rows behave like pre-v0.23
(no point tracking available, the four older sentinel modes
unchanged).

Revision ID: 0021_asset_point_tracking
Revises: 0020_watermark_presets
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_asset_point_tracking"
down_revision: str | None = "0020_watermark_presets"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("point_tracking_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("point_tracking_origin", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "point_tracking_origin")
    op.drop_column("assets", "point_tracking_json")
