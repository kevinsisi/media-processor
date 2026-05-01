"""Persistent app settings — DB-backed key/value store.

Used by ``services/settings_store.py`` to override env-var defaults at
runtime (e.g. the LLM API key pool managed via the Settings UI). Empty
or missing rows fall back to the env value, so containers boot fine on
a fresh database.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from media_processor.models.base import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
