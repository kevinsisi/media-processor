"""v0.25.1 — drafts.render_retry_count for the orphan-watchdog auto-retry.

The watchdog sweeps every 60 s for ``status in ('pending',
'processing')`` rows whose RQ job has disappeared (worker crash,
timeout, manual purge). On detection it re-enqueues the render and
increments this counter. After three failed auto-retries it gives
up and flips the row to ``failed`` so the operator sees a real
"任務已遺失" card instead of an indefinite re-enqueue loop.

The counter is reset to 0 every time the user explicitly triggers a
fresh render (initial trigger, re-render endpoint, reorder, rebuild
subtitles) so unrelated future failures get the full three-strike
budget.

Revision ID: 0023_draft_render_retry_count
Revises: 0022_project_bgm_fade_out
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_draft_render_retry_count"
down_revision: str | None = "0022_project_bgm_fade_out"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "drafts",
        sa.Column(
            "render_retry_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("drafts", "render_retry_count")
