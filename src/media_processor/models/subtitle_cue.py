"""SubtitleCue ORM entity — one row per cue, per draft.

Persisted by the orchestrator after the subtitles stage runs. The
``GET /drafts/{id}/subtitles`` and ``PATCH /drafts/{id}/subtitles/{idx}``
endpoints read/write this table; ``POST /drafts/{id}/rebuild-subtitles``
re-renders the SRT from these rows so the user's manual edits survive.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base

if TYPE_CHECKING:
    from media_processor.models.draft import Draft


class SubtitleCueRow(Base):
    """One subtitle cue belonging to a draft.

    ``idx`` is the 1-based SRT sequence number. ``start_ms`` / ``end_ms``
    are timeline-anchored (relative to the rendered mp4's start) — the
    same coordinates the burn-in stage uses.
    """

    __tablename__ = "subtitle_cues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
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

    draft: Mapped[Draft] = relationship("Draft", back_populates="subtitle_cues")

    __table_args__ = (
        UniqueConstraint("draft_id", "idx", name="uq_subtitle_cues_draft_idx"),
        CheckConstraint("start_ms < end_ms", name="ck_subtitle_cues_range"),
    )
