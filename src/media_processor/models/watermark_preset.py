"""WatermarkPreset ORM entity (v0.21.6).

A user-saved watermark configuration that can be applied to any
project. Each preset owns its own PNG file under
``${WATERMARK_DIR}/_presets/{preset_id}.png`` so the lifecycle is
independent of any single project — deleting a preset removes only
its own file; projects that already applied the preset keep their
own copy of the PNG.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from media_processor.models.base import Base


class WatermarkPreset(Base):
    __tablename__ = "watermark_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    position: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="bottom-right",
        server_default="bottom-right",
    )
    scale: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.10,
        server_default="0.10",
    )
    opacity: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
