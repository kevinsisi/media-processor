"""v0.43.x — fix stabilization_metrics_json column type from TEXT to JSON.

Migration 0030 accidentally used sa.Text() instead of sa.JSON() for
stabilization_metrics_json. This migration corrects the column type so
that the ORM and all 17+ other JSON columns in the schema use a
consistent type.

Revision ID: 0031_fix_stab_metrics_json
Revises: 0030_asset_stabilization_mode
Create Date: 2026-05-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_fix_stab_metrics_json"
down_revision: str | None = "0030_asset_stabilization_mode"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    if _is_sqlite():
        # SQLite has no ALTER COLUMN — batch mode rewrites the table.
        with op.batch_alter_table("assets") as batch:
            batch.alter_column(
                "stabilization_metrics_json",
                existing_type=sa.Text(),
                type_=sa.JSON(),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "assets",
            "stabilization_metrics_json",
            existing_type=sa.Text(),
            type_=sa.JSON(),
            existing_nullable=True,
            postgresql_using="stabilization_metrics_json::json",
        )


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("assets") as batch:
            batch.alter_column(
                "stabilization_metrics_json",
                existing_type=sa.JSON(),
                type_=sa.Text(),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "assets",
            "stabilization_metrics_json",
            existing_type=sa.JSON(),
            type_=sa.Text(),
            existing_nullable=True,
            postgresql_using="stabilization_metrics_json::text",
        )
