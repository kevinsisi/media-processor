"""SQLAlchemy ORM models for the media-processor pipeline.

Mirrors spec §4.1 — 9 entities covering project, asset, draft, review, and BGM.
Profile YAML files remain canonical on disk; the Profile table is a worker-side
read cache (see design D4).
"""

from media_processor.models.base import Base
from media_processor.models.bgm import BGM
from media_processor.models.draft import Draft, DraftSegment
from media_processor.models.enums import (
    DRAFT_STATUS_VALUES,
    PROJECT_STATUS_VALUES,
    REVIEW_ACTION_VALUES,
    DraftStatus,
    ProjectStatus,
    ReviewAction,
)
from media_processor.models.profile import Profile
from media_processor.models.project import Asset, AssetSegment, AssetTag, Project
from media_processor.models.review import Review

__all__ = [
    "BGM",
    "DRAFT_STATUS_VALUES",
    "PROJECT_STATUS_VALUES",
    "REVIEW_ACTION_VALUES",
    "Asset",
    "AssetSegment",
    "AssetTag",
    "Base",
    "Draft",
    "DraftSegment",
    "DraftStatus",
    "Profile",
    "Project",
    "ProjectStatus",
    "Review",
    "ReviewAction",
]
