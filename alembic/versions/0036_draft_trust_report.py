"""Add draft trust report storage.

Revision ID: 0036_draft_trust_report
Revises: 0035_asset_frame_analysis
Create Date: 2026-06-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_draft_trust_report"
down_revision: str | None = "0035_asset_frame_analysis"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("drafts") as batch:
        batch.add_column(sa.Column("trust_report_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("drafts") as batch:
        batch.drop_column("trust_report_json")
