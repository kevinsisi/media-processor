"""Project, Asset, AssetTag, AssetSegment ORM entities."""

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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from media_processor.models.base import Base
from media_processor.models.enums import (
    ASSET_STATUS_VALUES,
    PROJECT_STATUS_VALUES,
    TARGET_ASPECT_RATIO_VALUES,
    AssetStatus,
    ProjectStatus,
    TargetAspectRatio,
)

if TYPE_CHECKING:
    from media_processor.models.coverage import ScriptCoverage
    from media_processor.models.draft import Draft
    from media_processor.models.script import Script
    from media_processor.models.transcript import AssetTranscript


def _sql_in_list(values: tuple[str, ...]) -> str:
    """Build a literal SQL IN-list, e.g. ('a', 'b', 'c')."""
    quoted = ",".join(f"'{v}'" for v in values)
    return f"({quoted})"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    client: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_dir: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ProjectStatus.PENDING.value,
    )
    target_aspect_ratio: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        default=TargetAspectRatio.REELS.value,
        server_default=TargetAspectRatio.REELS.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    assets: Mapped[list[Asset]] = relationship(
        "Asset",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    drafts: Mapped[list[Draft]] = relationship(
        "Draft",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    script: Mapped[Script | None] = relationship(
        "Script",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN " + _sql_in_list(PROJECT_STATUS_VALUES),
            name="ck_projects_status",
        ),
        CheckConstraint(
            "target_aspect_ratio IN " + _sql_in_list(TARGET_ASPECT_RATIO_VALUES),
            name="ck_projects_target_aspect_ratio",
        ),
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=AssetStatus.PENDING.value,
    )
    analysis_steps_json: Mapped[Any] = mapped_column(JSON, nullable=True)

    project: Mapped[Project] = relationship("Project", back_populates="assets")
    tags: Mapped[list[AssetTag]] = relationship(
        "AssetTag",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    segments: Mapped[list[AssetSegment]] = relationship(
        "AssetSegment",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    transcript: Mapped[AssetTranscript | None] = relationship(
        "AssetTranscript",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    coverage: Mapped[ScriptCoverage | None] = relationship(
        "ScriptCoverage",
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN " + _sql_in_list(ASSET_STATUS_VALUES),
            name="ck_assets_status",
        ),
    )


class AssetTag(Base):
    __tablename__ = "asset_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tag_type: Mapped[str] = mapped_column(String(64), nullable=False)
    tag_name: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source_model: Mapped[str] = mapped_column(String(64), nullable=False)
    time_ranges_ms: Mapped[Any] = mapped_column(JSON, nullable=True)

    asset: Mapped[Asset] = relationship("Asset", back_populates="tags")

    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "tag_type",
            "tag_name",
            "source_model",
            name="uq_asset_tags_dedup",
        ),
    )


class AssetSegment(Base):
    __tablename__ = "asset_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    used_in_draft: Mapped[bool] = mapped_column(default=False, nullable=False)

    asset: Mapped[Asset] = relationship("Asset", back_populates="segments")

    __table_args__ = (CheckConstraint("start_ms < end_ms", name="ck_asset_segments_range"),)
