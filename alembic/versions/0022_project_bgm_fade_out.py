"""v0.24.0 — projects.bgm_fade_out_sec for the BGM tail-fade option.

Adds a single non-nullable float column with a 3.0-second server
default. The BGM mixer reads it on every render: ``> 0`` appends an
``afade=t=out`` filter onto the ducked BGM track so the music tapers
into silence over the last N seconds instead of cutting hard at the
end of the video. ``0`` keeps the pre-0.24.0 hard-cut behaviour.

Range is bounded UI-side to ``0..5`` seconds — short enough that the
fade doesn't eat into the meaningful tail of a 15-30 s reel, long
enough to feel intentional. The server-side ``0..10`` clamp on the
schema is the safety belt.

Revision ID: 0022_project_bgm_fade_out
Revises: 0021_asset_point_tracking
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_project_bgm_fade_out"
down_revision: str | None = "0021_asset_point_tracking"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "bgm_fade_out_sec",
            sa.Float(),
            nullable=False,
            server_default="3.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "bgm_fade_out_sec")
