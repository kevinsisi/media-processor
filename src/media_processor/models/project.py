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
    # M6.4 — optional BGM track for the auto-edit mixer. Path is to the
    # uploaded audio file under ``BGM_DIR``; null means "no BGM, the bgm
    # render stage is a no-op copy".
    bgm_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # v0.18 — optional brand watermark / logo overlay burned into the
    # final mp4. ``watermark_path`` is the on-disk PNG under
    # ``WATERMARK_DIR``; null means the overlay stage is a no-op. The
    # other three columns describe the layout — they're kept even when
    # the file is removed so a re-upload picks up the previous setup.
    watermark_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    watermark_position: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="bottom-right",
        server_default="bottom-right",
    )
    watermark_scale: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.10,
        server_default="0.10",
    )
    watermark_opacity: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default="1.0",
    )
    # v0.18 — user-customisable subtitle burn-in style. The renderer reads
    # these to build the drawtext filter; defaults match the historic
    # SUBTITLE_FONT_PATH / white-on-black / bottom-anchored look so a
    # project that hasn't been touched renders identically.
    # ``font`` keys into video_renderer.SUBTITLE_FONT_CHOICES (Noto Sans CJK
    # TC / Noto Sans CJK TC Bold / Noto Serif CJK TC); ``position`` is one
    # of "top" / "middle" / "bottom"; ``size`` is one of "small" / "medium"
    # / "large"; ``outline_width`` is one of "none" / "thin" / "thick".
    # Colours are stored as hex like ``#ffffff`` and converted to drawtext's
    # 0xRRGGBB form at render time.
    subtitle_font: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="noto_sans_tc",
        server_default="noto_sans_tc",
    )
    subtitle_color: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="#ffffff",
        server_default="#ffffff",
    )
    subtitle_outline_color: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="#000000",
        server_default="#000000",
    )
    subtitle_position: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="bottom",
        server_default="bottom",
    )
    subtitle_size: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="medium",
        server_default="medium",
    )
    subtitle_outline_width: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="thin",
        server_default="thin",
    )
    # v0.21 — optional "subject class" the planner should bias toward.
    # ``None`` = 不限 (legacy behaviour, no subject filtering). When set
    # to a COCO-80 class name (e.g. ``"person"``) the edit_planner shrinks
    # each chosen segment's [asset_start_ms, asset_end_ms) window to where
    # the subject actually appears (with a ±500ms tolerance) and demotes
    # assets that don't contain the class to last-resort priority.
    subject_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    # v0.16 — YOLOv8 per-frame bounding boxes. v0.17 widened from a single
    # dominant track to a multi-track structure so the user can pick a
    # specific object on the analysis page.
    # Shape:
    #   {"subject_class": "car", "confidence": 0.92,
    #    "src_w": 1920, "src_h": 1080, "fps": 5.0,
    #    "tracks": [{"object_index": 0, "cls_name": "car", "confidence": 0.92,
    #                "area_score": 0.42,
    #                "frames": [{"t_ms": 0, "x": 870, "y": 420, "w": 180, "h": 240}, …]},
    #               …],
    #    "frames": [...]}  # legacy: dominant track frames, kept for compat
    # ``None`` means the tracking step hasn't run (or saw no detections).
    # Read by services.auto_reframe to compute per-frame crop windows
    # so the renderer can keep the subject centered in 9:16 output.
    tracking_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    # v0.17 — user override for which tracked object the renderer should
    # follow. ``None`` = auto (largest by area, the historic default).
    # ``>= 0`` = the ``object_index`` inside ``tracking_json["tracks"]``.
    # ``-1`` = use ``custom_roi_json`` (CSRT-tracked user-drawn ROI).
    # ``-2`` = fixed framing — no auto-reframe, static centered crop.
    # ``-3`` = no auto-reframe and no fallback (original aspect crop).
    tracked_object_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # v0.17 — CSRT-tracked custom ROI when ``tracked_object_index == -1``.
    # Same per-frame bbox shape as one entry in ``tracking_json["tracks"]``;
    # we store it on its own column so a re-run of YOLO can't clobber it.
    # Shape: {"src_w": 1920, "src_h": 1080, "fps": 5.0,
    #         "frames": [{"t_ms": 0, "x": 870, "y": 420, "w": 180, "h": 240}, …]}
    custom_roi_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    # v0.18 — secondary-language subtitle marker. ``None`` = no translation
    # has been generated. ``"en"`` (current sole supported value) = the
    # asset has been run through Whisper task="translate" and the resulting
    # English segments are stored in ``subtitle_secondary_segments_json``.
    subtitle_secondary_lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # v0.18 — translated transcript segments produced by Whisper translate.
    # Same SRT-style shape as ``AssetTranscript.segments_json``:
    #   [{"idx": int, "start_ms": int, "end_ms": int, "text": str}, …]
    # Kept on Asset (not AssetTranscript) so the secondary track is
    # independent of the primary STT row — re-running STT won't drop the
    # translation, and re-running translation won't touch the zh-Hant.
    subtitle_secondary_segments_json: Mapped[Any] = mapped_column(JSON, nullable=True)

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
