"""v0.30.0 — projects.smart_camera_enabled (opt-in AI Smart Camera).

Adds a single non-null Boolean column with a server default of ``0``
(``False``) so existing rows pick up the safe default — the whole
feature is opt-in by design (Gemini quota cost + camera-move
surprise factor for operators who wanted a static look).

SQLite test backend gets ``batch_alter_table`` because in-place
``ALTER TABLE ... ADD COLUMN ... NOT NULL`` is not supported there;
Postgres prod takes the direct ``add_column`` path. ``server_default``
covers the existing rows; the orm-side ``default=False`` covers fresh
inserts.

Revision ID: 0027_project_smart_camera
Revises: 0026_aspect_ratios_redux
Create Date: 2026-05-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_project_smart_camera"
down_revision: str | None = "0026_aspect_ratios_redux"
branch_labels: str | None = None
depends_on: str | None = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    column = sa.Column(
        "smart_camera_enabled",
        sa.Boolean(),
        nullable=False,
        server_default=sa.text("0"),
    )
    if _is_sqlite():
        with op.batch_alter_table("projects") as batch:
            batch.add_column(column)
    else:
        op.add_column("projects", column)


def downgrade() -> None:
    if _is_sqlite():
        with op.batch_alter_table("projects") as batch:
            batch.drop_column("smart_camera_enabled")
    else:
        op.drop_column("projects", "smart_camera_enabled")
