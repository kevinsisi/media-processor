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


PROJECT_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in ProjectStatus)
DRAFT_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in DraftStatus)
REVIEW_ACTION_VALUES: tuple[str, ...] = tuple(a.value for a in ReviewAction)
