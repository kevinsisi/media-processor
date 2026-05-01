"""AssetTranscript ORM entity — 1:1 with Asset, holds zh-Hant STT output."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base

if TYPE_CHECKING:
    from media_processor.models.project import Asset


class AssetTranscript(Base):
    """One transcript row per Asset.

    ``segments_json`` is the SRT-style structure
    ``[{idx, start_ms, end_ms, text}, ...]``. ``transcript_text`` is the
    joined segment texts separated by ``\\n`` — kept on the row so callers
    can read the plain text without parsing JSON.
    """

    __tablename__ = "asset_transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-Hant")
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    transcript_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    segments_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    edited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    asset: Mapped[Asset] = relationship("Asset", back_populates="transcript")

    __table_args__ = (UniqueConstraint("asset_id", name="uq_asset_transcripts_asset_id"),)
