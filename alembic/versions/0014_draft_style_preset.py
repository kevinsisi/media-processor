"""v0.18 — drafts.style_preset column + check constraint.

Per-draft clip-style preset (fast / slow / commercial / artistic /
custom). The orchestrator uses it to bias planner span bounds,
transition allowlist, and the music-suggestion prompt. Existing rows
default to ``custom`` so legacy drafts keep their pre-preset behaviour.

Revision ID: 0014_draft_style_preset
Revises: 0013_draft_segment_volume
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_draft_style_preset"
down_revision: str | None = "0013_draft_segment_volume"
branch_labels: str | None = None
depends_on: str | None = None


_VALUES = ("fast", "slow", "commercial", "artistic", "custom")


def upgrade() -> None:
    op.add_column(
        "drafts",
        sa.Column(
            "style_preset",
            sa.String(length=32),
            nullable=False,
            server_default="custom",
        ),
    )
    op.create_check_constraint(
        "ck_drafts_style_preset",
        "drafts",
        "style_preset IN (" + ",".join(f"'{v}'" for v in _VALUES) + ")",
    )


def downgrade() -> None:
    op.drop_constraint("ck_drafts_style_preset", "drafts", type_="check")
    op.drop_column("drafts", "style_preset")
