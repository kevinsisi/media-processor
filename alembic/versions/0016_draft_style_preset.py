"""v0.19 — drafts.style_preset column + check constraint.

Per-draft clip-style preset (fast / slow / commercial / artistic /
custom). The orchestrator uses it to bias planner span bounds,
transition allowlist, and the music-suggestion prompt. Existing rows
default to ``custom`` so legacy drafts keep their pre-preset behaviour.

Revision ID: 0016_draft_style_preset
Revises: 0015_project_subtitle_style
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_draft_style_preset"
down_revision: str | None = "0015_project_subtitle_style"
branch_labels: str | None = None
depends_on: str | None = None


_VALUES = ("fast", "slow", "commercial", "artistic", "custom")


def upgrade() -> None:
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "style_preset",
                sa.String(length=32),
                nullable=False,
                server_default="custom",
            ),
        )
        batch_op.create_check_constraint(
            "ck_drafts_style_preset",
            "style_preset IN (" + ",".join(f"'{v}'" for v in _VALUES) + ")",
        )


def downgrade() -> None:
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.drop_constraint("ck_drafts_style_preset", type_="check")
        batch_op.drop_column("style_preset")
