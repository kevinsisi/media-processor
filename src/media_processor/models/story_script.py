"""StoryScript ORM entity for Narrato-style short-form planning."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base

if TYPE_CHECKING:
    from media_processor.models.draft import Draft
    from media_processor.models.project import Project


class StoryScript(Base):
    """Versioned Story/Narrato script artifact for a project or draft."""

    __tablename__ = "story_scripts"

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
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    script_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
