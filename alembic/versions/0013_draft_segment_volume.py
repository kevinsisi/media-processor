"""v0.17 — draft_segments.voice_volume + bgm_volume columns.

Per-segment audio gain so the user can boost / mute the original voice
and override the auto BGM ducking on individual cuts.

Revision ID: 0013_draft_segment_volume
Revises: 0012_asset_tracking_target
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_draft_segment_volume"
down_revision: str | None = "0012_asset_tracking_target"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "draft_segments",
        sa.Column(
            "voice_volume",
            sa.Float(),
            nullable=False,
            server_default="1.0",
        ),
    )
    op.add_column(
        "draft_segments",
        sa.Column("bgm_volume", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("draft_segments", "bgm_volume")
    op.drop_column("draft_segments", "voice_volume")
