"""v0.42.4 — persist per-variant asset analysis snapshots.

Revision ID: 0029_asset_variant_analysis_snapshots
Revises: 0028_asset_stabilized_variants
Create Date: 2026-05-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_asset_variant_analysis_snapshots"
down_revision: str | None = "0028_asset_stabilized_variants"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    column = sa.Column("variant_analysis_json", sa.JSON(), nullable=True)
    if _is_sqlite():
        with op.batch_alter_table("assets") as batch:
            batch.add_column(column)
    else:
        op.add_column("assets", column)


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("assets") as batch:
            batch.drop_column("variant_analysis_json")
    else:
        op.drop_column("assets", "variant_analysis_json")
