"""SQLAlchemy ORM models for the media-processor pipeline.

M2 §4.1 set up the original 9 entities (project / asset / draft / review / bgm
+ profile-cache); M3 added scripts + upload_sessions; M4 adds asset_transcripts
+ script_coverage and extends Asset with analysis bookkeeping.
"""

from media_processor.models.app_setting import AppSetting
from media_processor.models.base import Base
from media_processor.models.bgm import BGM
from media_processor.models.bgm_generation import BgmGenerationJob
from media_processor.models.coverage import ScriptCoverage
from media_processor.models.draft import Draft, DraftSegment
from media_processor.models.draft_comment import DraftComment
from media_processor.models.enums import (
    ANALYSIS_STEP_VALUES,
    ASSET_STATUS_VALUES,
    CLIP_STYLE_PRESET_VALUES,
    CUT_SOURCE_KIND_VALUES,
    DRAFT_STATUS_VALUES,
    EDIT_STEP_VALUES,
    PROJECT_STATUS_VALUES,
    REVIEW_ACTION_VALUES,
    TARGET_ASPECT_RATIO_VALUES,
    UPLOAD_KIND_VALUES,
    UPLOAD_STATUS_VALUES,
    AnalysisStep,
    AnalysisStepState,
    AssetStatus,
    ClipStylePreset,
    CutSourceKind,
    DraftStatus,
    EditStep,
    ProjectStatus,
    ReviewAction,
    TargetAspectRatio,
    UploadKind,
    UploadStatus,
)
from media_processor.models.profile import Profile
from media_processor.models.project import Asset, AssetSegment, AssetTag, Project
from media_processor.models.review import Review
from media_processor.models.script import Script
from media_processor.models.subtitle_cue import SubtitleCueRow
from media_processor.models.transcript import AssetTranscript
from media_processor.models.upload_session import UploadSession
from media_processor.models.watermark_preset import WatermarkPreset

__all__ = [
    "ANALYSIS_STEP_VALUES",
    "ASSET_STATUS_VALUES",
    "AppSetting",
    "BGM",
    "BgmGenerationJob",
    "CLIP_STYLE_PRESET_VALUES",
    "CUT_SOURCE_KIND_VALUES",
    "DRAFT_STATUS_VALUES",
    "EDIT_STEP_VALUES",
    "PROJECT_STATUS_VALUES",
    "REVIEW_ACTION_VALUES",
    "TARGET_ASPECT_RATIO_VALUES",
    "UPLOAD_KIND_VALUES",
    "UPLOAD_STATUS_VALUES",
    "AnalysisStep",
    "AnalysisStepState",
    "Asset",
    "AssetSegment",
    "AssetStatus",
    "AssetTag",
    "AssetTranscript",
    "Base",
    "ClipStylePreset",
    "CutSourceKind",
    "Draft",
    "DraftComment",
    "DraftSegment",
    "DraftStatus",
    "EditStep",
    "Profile",
    "Project",
    "ProjectStatus",
    "Review",
    "ReviewAction",
    "Script",
    "ScriptCoverage",
    "SubtitleCueRow",
    "TargetAspectRatio",
    "UploadKind",
    "UploadSession",
    "UploadStatus",
    "WatermarkPreset",
]
