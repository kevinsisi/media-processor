"""ScriptCoverage ORM entity — semantic compare of transcript vs project script."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base

if TYPE_CHECKING:
    from media_processor.models.project import Asset


class ScriptCoverage(Base):
    """Per-asset coverage row, paired against the project's current Script.

    Replaced rather than versioned: editing a script invalidates (deletes)
    these rows; editing a transcript does not — the operator re-triggers
    coverage explicitly. ``match_details_json`` is the validated array of
    per-transcript-segment matches the model returned.
    """

    __tablename__ = "script_coverage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    script_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("scripts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    scripted_segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_ratio_by_count: Mapped[float] = mapped_column(Float, nullable=False)
    coverage_ratio_by_duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    match_details_json: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    asset: Mapped[Asset] = relationship("Asset", back_populates="coverage")

    __table_args__ = (UniqueConstraint("asset_id", name="uq_script_coverage_asset_id"),)
