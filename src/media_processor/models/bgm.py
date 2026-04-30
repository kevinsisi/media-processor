"""BGM ORM entity."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from media_processor.models.base import Base


class BGM(Base):
    __tablename__ = "bgms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    bpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    beat_grid_json: Mapped[Any] = mapped_column(JSON, nullable=True)
