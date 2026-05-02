"""Draft and DraftSegment ORM entities."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base
from media_processor.models.enums import DRAFT_STATUS_VALUES, DraftStatus

if TYPE_CHECKING:
    from media_processor.models.draft_comment import DraftComment
    from media_processor.models.project import AssetSegment, Project
    from media_processor.models.review import Review
    from media_processor.models.subtitle_cue import SubtitleCueRow


def _in_list(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


class Draft(Base):
    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DraftStatus.PENDING.value,
    )
    output_zip_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mp4_preview_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    subtitle_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # v0.16.2 — snapshot of the BGM file this draft was rendered with.
    # First render copies project.bgm_path here; subsequent re-renders
    # (e.g. timeline reorder) reuse this path even after the user
    # generates a new AI track on the project, so each draft keeps
    # whichever BGM it actually shipped with.
    bgm_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress_steps_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    cut_plan_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    project: Mapped[Project] = relationship("Project", back_populates="drafts")
    segments: Mapped[list[DraftSegment]] = relationship(
        "DraftSegment",
        back_populates="draft",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DraftSegment.order",
    )
    reviews: Mapped[list[Review]] = relationship(
        "Review",
        back_populates="draft",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    comments: Mapped[list[DraftComment]] = relationship(
        "DraftComment",
        back_populates="draft",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DraftComment.created_at",
    )
    subtitle_cues: Mapped[list[SubtitleCueRow]] = relationship(
        "SubtitleCueRow",
        back_populates="draft",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SubtitleCueRow.idx",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_drafts_project_version"),
        CheckConstraint("version >= 1", name="ck_drafts_version_positive"),
        CheckConstraint(
            "status IN " + _in_list(DRAFT_STATUS_VALUES),
            name="ck_drafts_status",
        ),
    )


class DraftSegment(Base):
    __tablename__ = "draft_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("drafts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    # Pre-M5 callers (the legacy heuristic planner) populated `asset_segment_id`
    # against an existing AssetSegment row. M5's Gemini planner picks segments
    # straight from transcripts and stores `asset_id` + `asset_start_ms` /
    # `asset_end_ms` directly. New rows MUST set asset_id; either path can
    # populate the on-timeline range.
    asset_segment_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("asset_segments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    asset_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    asset_start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    asset_end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    on_timeline_start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    on_timeline_end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    reframe_keyframes: Mapped[Any] = mapped_column(JSON, nullable=True)
    transition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blurred_source_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    plan_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    draft: Mapped[Draft] = relationship("Draft", back_populates="segments")
    asset_segment: Mapped[AssetSegment | None] = relationship("AssetSegment")

    __table_args__ = (
        UniqueConstraint("draft_id", "order", name="uq_draft_segments_order"),
        CheckConstraint(
            "on_timeline_start_ms < on_timeline_end_ms",
            name="ck_draft_segments_range",
        ),
        CheckConstraint(
            "asset_start_ms IS NULL OR asset_end_ms IS NULL OR asset_start_ms < asset_end_ms",
            name="ck_draft_segments_asset_range",
        ),
    )
