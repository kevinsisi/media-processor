"""v0.43.4 — allow upload sessions above 2 GiB.

Revision ID: 0032_upload_total_size_bigint
Revises: 0031_fix_stab_metrics_json
Create Date: 2026-05-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_upload_total_size_bigint"
down_revision: str | None = "0031_fix_stab_metrics_json"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("upload_sessions") as batch:
        batch.alter_column(
            "total_size",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("upload_sessions") as batch:
        batch.alter_column(
            "total_size",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
