"""Enumerations used by ORM entities."""

from __future__ import annotations

from enum import StrEnum


class ProjectStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DEGRADED = "degraded"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    FAILED = "failed"


class DraftStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY_FOR_REVIEW = "ready_for_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class ReviewAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REPATCH = "repatch"
    DOWNLOAD = "download"


class TargetAspectRatio(StrEnum):
    """v0.29.0 — supported output aspect ratios.

    Pre-0.29 the enum carried four IG-feed-friendly variants; in
    practice operators only shipped 9:16. The 4:5 / 1:1 IG-feed
    sizes were dropped and a horizontal 16:9 added for YouTube /
    desktop-feed / web-embed deliverables. Legacy 4:5 / 1:1 rows
    are migrated to 9:16 by alembic 0026.
    """

    REELS = "9:16"
    LANDSCAPE = "16:9"


class UploadKind(StrEnum):
    VIDEO = "video"
    SCRIPT = "script"


class UploadStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    ABORTED = "aborted"


class AssetStatus(StrEnum):
    """Lifecycle of an Asset row through M3 ingestion + M4 analysis."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    ANALYSIS_FAILED = "analysis_failed"


class AnalysisStep(StrEnum):
    """Per-step keys inside Asset.analysis_steps_json."""

    STT = "stt"
    SCENE = "scene"
    MOTION = "motion"
    EMOTION = "emotion"  # Phase 8.1 — MediaPipe face landmarker → emotion tags
    TRACKING = "tracking"  # v0.16 — YOLOv8 per-frame subject bbox → tracking_json
    COVERAGE = "coverage"


class AnalysisStepState(StrEnum):
    """Per-step state values inside Asset.analysis_steps_json.

    Failure states are stored as the literal string ``failed:{reason}`` and
    are not represented here — only the three canonical non-failed states
    are enumerated. Use ``state.startswith("failed:")`` to detect failures.
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"


class EditStep(StrEnum):
    """Per-stage keys inside Draft.progress_steps_json (M5 auto-edit)."""

    PLAN = "plan"
    CUT = "cut"
    STABILIZE = "stabilize"  # v0.14.3 — vidstabdetect + vidstabtransform two-pass
    CONCAT = "concat"
    SUBTITLES = "subtitles"
    BGM = "bgm"  # M6.4 — voice-ducked background music mix; no-op without bgm_path


class CutSourceKind(StrEnum):
    """`source_kind` values for DraftSegment / CutPlanSegment."""

    SCRIPTED = "scripted"
    IMPROV = "improv"


class ClipStylePreset(StrEnum):
    """v0.18 — preset bundles that bias planner span / transition / BGM hints.

    ``custom`` keeps the legacy behaviour (planner picks freely within the
    project profile defaults). The other four bias the per-asset Gemini
    prompt + local assembly toward a target rhythm and feed a one-line
    musical hint into the music-suggestion endpoint.
    """

    FAST = "fast"
    SLOW = "slow"
    COMMERCIAL = "commercial"
    ARTISTIC = "artistic"
    CUSTOM = "custom"


class EditMode(StrEnum):
    """Draft-scoped creative direction selected when starting an edit."""

    STANDARD = "standard"
    LUXURY_AUTO = "luxury_auto"
    VIRAL_SHORT = "viral_short"
    STORY = "story"
    DOCUMENTARY = "documentary"   # NarratoAI: frame analysis → narration → TTS
    DRAMA_EXPLAIN = "drama_explain"  # NarratoAI: transcript → drama explanation → TTS


PROJECT_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in ProjectStatus)
DRAFT_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in DraftStatus)
REVIEW_ACTION_VALUES: tuple[str, ...] = tuple(a.value for a in ReviewAction)
TARGET_ASPECT_RATIO_VALUES: tuple[str, ...] = tuple(a.value for a in TargetAspectRatio)
UPLOAD_KIND_VALUES: tuple[str, ...] = tuple(k.value for k in UploadKind)
UPLOAD_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in UploadStatus)
ASSET_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in AssetStatus)
ANALYSIS_STEP_VALUES: tuple[str, ...] = tuple(s.value for s in AnalysisStep)
EDIT_STEP_VALUES: tuple[str, ...] = tuple(s.value for s in EditStep)
CUT_SOURCE_KIND_VALUES: tuple[str, ...] = tuple(s.value for s in CutSourceKind)
CLIP_STYLE_PRESET_VALUES: tuple[str, ...] = tuple(s.value for s in ClipStylePreset)
EDIT_MODE_VALUES: tuple[str, ...] = tuple(s.value for s in EditMode)
