"""v0.19 — secondary-language subtitles (Whisper translate).

Adds:
  * ``assets.subtitle_secondary_lang`` (str, nullable) — marker for which
    secondary language has been generated (``"en"`` for English).
  * ``assets.subtitle_secondary_segments_json`` (JSON, nullable) —
    translated SRT-style segments produced by Whisper task="translate".
  * ``draft_segments.subtitle_secondary_text`` (Text, nullable) — per-cut
    snapshot of the secondary subtitle text, written by the orchestrator
    from the clipped asset translation when the draft is rendered.

Revision ID: 0017_secondary_subtitles
Revises: 0016_draft_style_preset
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_secondary_subtitles"
down_revision: str | None = "0016_draft_style_preset"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("subtitle_secondary_lang", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column("subtitle_secondary_segments_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "draft_segments",
        sa.Column("subtitle_secondary_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("draft_segments", "subtitle_secondary_text")
    op.drop_column("assets", "subtitle_secondary_segments_json")
    op.drop_column("assets", "subtitle_secondary_lang")
