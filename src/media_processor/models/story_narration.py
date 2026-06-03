"""Generated StoryScript narration audio artifact records."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base

if TYPE_CHECKING:
    from media_processor.models.draft import Draft
    from media_processor.models.project import Project
    from media_processor.models.story_script import StoryScript


class StoryNarrationAsset(Base):
    """One generated TTS audio file for a StoryScript item."""

    __tablename__ = "story_narration_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    draft_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("drafts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    story_script_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("story_scripts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    story_item_order: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    narration_text_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    voice: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    project: Mapped[Project] = relationship("Project")
    draft: Mapped[Draft | None] = relationship("Draft")
    story_script: Mapped[StoryScript | None] = relationship("StoryScript")

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "story_script_id",
            "story_item_order",
            "narration_text_hash",
            "provider",
            "voice",
            name="uq_story_narration_identity",
        ),
        Index(
            "ix_story_narration_reuse",
            "project_id",
            "story_item_order",
            "narration_text_hash",
            "provider",
            "voice",
            "status",
        ),
    )
