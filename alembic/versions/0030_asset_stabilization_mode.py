"""v0.43.x — add stabilization_mode and stabilization_metrics_json to assets.

Revision ID: 0030_asset_stabilization_mode
Revises: 0029_asset_variant_analysis_snapshots
Create Date: 2026-05-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_asset_stabilization_mode"
down_revision: str | None = "0029_asset_var_snapshots"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    columns = [
        sa.Column("stabilization_mode", sa.String(length=32), nullable=True),
        sa.Column("stabilization_metrics_json", sa.Text(), nullable=True),
    ]
    if _is_sqlite():
        with op.batch_alter_table("assets") as batch:
            for column in columns:
                batch.add_column(column)
    else:
        for column in columns:
            op.add_column("assets", column)


def downgrade() -> None:
    names = ["stabilization_metrics_json", "stabilization_mode"]
    if _is_sqlite():
        with op.batch_alter_table("assets") as batch:
            for name in names:
                batch.drop_column(name)
    else:
        for name in names:
            op.drop_column("assets", name)
