"""v0.28.0 — assets.point_tracking_status + .point_tracking_error.

v0.27.3 ran the LK loop synchronously inside the API endpoint with a
30-second wall-clock budget. Operators with long / high-resolution
assets (1728x3072 portrait, 2-min clips) blew past the budget and
got 504s. The whole point of pixel-precise tracking is the operator
has decided this asset deserves manual intervention — falling back
to "use a different mode" defeats the purpose.

v0.28.0 moves the LK loop to an RQ job on the analysis queue. The
endpoint enqueues + returns immediately; the FE polls. The two new
columns drive the FE state machine:

  * ``point_tracking_status``: NULL (never tried) / "pending" /
    "done" / "failed". The FE renders "追蹤分析中…" while pending
    and the crosshair when done; "failed" surfaces an error toast.
  * ``point_tracking_error``: free-form reason string when status
    is "failed" (e.g. "OpenCV could not open /app/media/...").

Both nullable; existing rows behave like pre-v0.28 (NULL status is
treated identically to "done" by the renderer — its only check is
``point_tracking_json is not None``, which is unchanged).

Revision ID: 0024_asset_point_tracking_status
Revises: 0023_draft_render_retry_count
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_asset_point_tracking_status"
down_revision: str | None = "0023_draft_render_retry_count"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("point_tracking_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("point_tracking_error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "point_tracking_error")
    op.drop_column("assets", "point_tracking_status")
