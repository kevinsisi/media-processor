"""DraftComment — per-version discussion thread for the M5.2 review flow.

Each Draft is an immutable render version; the comments live alongside so a
team can leave per-version feedback without touching the canonical Review
record (which is a structured approve/reject decision, not a freeform chat).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base

if TYPE_CHECKING:
    from media_processor.models.draft import Draft


class DraftComment(Base):
    __tablename__ = "draft_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author: Mapped[str] = mapped_column(String(64), nullable=False, default="anonymous")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    draft: Mapped[Draft] = relationship("Draft", back_populates="comments")
