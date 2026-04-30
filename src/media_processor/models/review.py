"""Review ORM entity."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base
from media_processor.models.enums import REVIEW_ACTION_VALUES

if TYPE_CHECKING:
    from media_processor.models.draft import Draft


def _in_list(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer: Mapped[str] = mapped_column(String(64), nullable=False, default="alice")
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    draft: Mapped[Draft] = relationship("Draft", back_populates="reviews")

    __table_args__ = (
        CheckConstraint(
            "action IN " + _in_list(REVIEW_ACTION_VALUES),
            name="ck_reviews_action",
        ),
    )
