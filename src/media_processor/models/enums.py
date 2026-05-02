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
    REELS = "9:16"
    FEED_PORTRAIT = "4:5"
    FEED_SQUARE = "1:1"


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
