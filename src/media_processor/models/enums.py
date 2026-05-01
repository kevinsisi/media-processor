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


PROJECT_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in ProjectStatus)
DRAFT_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in DraftStatus)
REVIEW_ACTION_VALUES: tuple[str, ...] = tuple(a.value for a in ReviewAction)
TARGET_ASPECT_RATIO_VALUES: tuple[str, ...] = tuple(a.value for a in TargetAspectRatio)
UPLOAD_KIND_VALUES: tuple[str, ...] = tuple(k.value for k in UploadKind)
UPLOAD_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in UploadStatus)
