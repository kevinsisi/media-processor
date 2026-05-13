"""v0.40.0 — asset-level stabilized source variants.

Revision ID: 0028_asset_stabilized_variants
Revises: 0027_project_smart_camera
Create Date: 2026-05-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_asset_stabilized_variants"
down_revision: str | None = "0027_project_smart_camera"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    columns = [
        sa.Column("stabilized_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "stabilization_status",
            sa.String(length=16),
            nullable=False,
            server_default="not_started",
        ),
        sa.Column("stabilization_error", sa.Text(), nullable=True),
        sa.Column(
            "active_asset_variant",
            sa.String(length=16),
            nullable=False,
            server_default="raw",
        ),
    ]
    if _is_sqlite():
        with op.batch_alter_table("assets") as batch:
            for column in columns:
                batch.add_column(column)
    else:
        for column in columns:
            op.add_column("assets", column)


def downgrade() -> None:
    names = [
        "active_asset_variant",
        "stabilization_error",
        "stabilization_status",
        "stabilized_path",
    ]
    if _is_sqlite():
        with op.batch_alter_table("assets") as batch:
            for name in names:
                batch.drop_column(name)
    else:
        for name in names:
            op.drop_column("assets", name)
