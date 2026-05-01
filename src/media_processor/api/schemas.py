"""Pydantic response/request schemas for the M2 API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from media_processor.models.enums import REVIEW_ACTION_VALUES

ReviewActionLiteral = Literal["approve", "reject", "repatch", "download"]
TargetAspectRatioLiteral = Literal["9:16", "4:5", "1:1"]
UploadKindLiteral = Literal["video", "script"]


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    client: str | None
    profile_name: str
    status: str
    target_aspect_ratio: str
    created_at: datetime
    asset_count: int
    latest_draft_version: int | None


class ProjectDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    client: str | None
    profile_name: str
    source_dir: str
    status: str
    target_aspect_ratio: str
    created_at: datetime
    asset_count: int
    draft_count: int


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    client: str | None = Field(default=None, max_length=255)
    profile_name: str = Field(..., min_length=1, max_length=128)
    target_aspect_ratio: TargetAspectRatioLiteral = "9:16"


class ScriptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    body: str
    source_filename: str | None
    updated_at: datetime


class ScriptUpsert(BaseModel):
    body: str = Field(..., max_length=1_048_576)
    source_filename: str | None = Field(default=None, max_length=255)


class UploadSessionCreate(BaseModel):
    kind: UploadKindLiteral
    filename: str = Field(..., min_length=1, max_length=512)
    total_size: int = Field(..., ge=0)
    chunk_size: int = Field(..., gt=0, le=64 * 1024 * 1024)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class UploadSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    kind: str
    filename: str
    total_size: int
    chunk_size: int
    received_chunks: list[int]
    status: str


class UploadCompleteOut(BaseModel):
    session: UploadSessionOut
    asset: "AssetDetail | None" = None
    script: ScriptOut | None = None


class DraftSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    profile_name: str
    version: int
    status: str
    output_zip_path: str | None
    mp4_preview_path: str | None
    ai_score: float | None
    created_at: datetime


class DraftSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    order: int
    asset_segment_id: int
    on_timeline_start_ms: int
    on_timeline_end_ms: int
    transition: str | None


class DraftDetail(DraftSummary):
    segments: list[DraftSegmentOut]


class AssetTagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tag_type: str
    tag_name: str
    confidence: float
    source_model: str
    time_ranges_ms: Any | None


class AssetDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    file_path: str
    duration_ms: int
    resolution: str | None
    fps: float | None
    codec: str | None
    sha256: str
    thumbnail_path: str | None
    status: str
    tags: list[AssetTagOut]
    analysis_steps: dict[str, str] | None = None


# ----- M4 — transcript / coverage / analyze schemas -----


class TranscriptSegmentOut(BaseModel):
    """One SRT-style segment as returned by /assets/{id}/transcript."""

    idx: int
    start_ms: int
    end_ms: int
    text: str


class TranscriptSegmentIn(BaseModel):
    """One SRT-style segment in a PUT body. ``idx`` is reassigned server-side."""

    start_ms: int = Field(..., ge=0)
    end_ms: int = Field(..., gt=0)
    text: str = Field(..., max_length=10_000)


class TranscriptOut(BaseModel):
    asset_id: int
    language: str
    model: str
    transcript_text: str
    segments: list[TranscriptSegmentOut]
    edited: bool
    created_at: datetime
    updated_at: datetime


class TranscriptUpsert(BaseModel):
    """Body for PUT /assets/{id}/transcript — replaces all segments."""

    segments: list[TranscriptSegmentIn] = Field(..., max_length=10_000)


class CoverageMatchOut(BaseModel):
    transcript_idx: int
    classification: Literal["scripted", "improvised"]
    confidence: float
    matched_script_excerpt: str


class ScriptCoverageOut(BaseModel):
    asset_id: int
    script_id: int
    model: str
    scripted_segment_count: int
    total_segment_count: int
    coverage_ratio_by_count: float
    coverage_ratio_by_duration_ms: float
    matches: list[CoverageMatchOut]
    computed_at: datetime


class AnalyzeRequest(BaseModel):
    """Body for POST /assets/{id}/analyze — both fields optional."""

    steps: list[Literal["stt", "scene", "motion", "coverage"]] | None = None
    force: bool = False


class AnalyzeResponse(BaseModel):
    """Returned by POST /assets/{id}/analyze (202 Accepted)."""

    asset_id: int
    job_id: str
    status: str
    analysis_steps: dict[str, str]


# ----- M4 — project analysis page polling endpoint -----


class TranscriptSummaryOut(BaseModel):
    """Compact transcript info embedded in the assets-page list."""

    segment_count: int
    edited: bool
    updated_at: datetime


class CoverageSummaryOut(BaseModel):
    """Compact coverage info embedded in the assets-page list."""

    coverage_ratio_by_count: float
    coverage_ratio_by_duration_ms: float
    scripted_segment_count: int
    total_segment_count: int


class MotionSegmentOut(BaseModel):
    motion_type: Literal["pan", "tilt", "zoom", "static", "handheld"]
    start_ms: int
    end_ms: int


class SceneTagOut(BaseModel):
    name: str
    confidence: float


class AssetAnalysisItem(BaseModel):
    """One row for the project-analysis page polling list."""

    id: int
    file_path: str
    filename: str
    duration_ms: int
    status: str
    analysis_steps: dict[str, str] | None
    transcript_summary: TranscriptSummaryOut | None
    coverage_summary: CoverageSummaryOut | None
    scene_tags: list[SceneTagOut]
    motion_segments: list[MotionSegmentOut]


class ProjectAnalysisOut(BaseModel):
    """Returned by GET /projects/{id}/assets — drives the polling page."""

    project: ProjectDetail
    has_script: bool
    assets: list[AssetAnalysisItem]


class ReviewCreate(BaseModel):
    draft_id: int
    action: ReviewActionLiteral = Field(..., description=f"One of {REVIEW_ACTION_VALUES}")
    prompt_feedback: str | None = None
    reviewer: str = "alice"


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    draft_id: int
    reviewer: str
    action: str
    prompt_feedback: str | None
    reviewed_at: datetime


class DraftPatchRequest(BaseModel):
    """Body for POST /drafts/{id}/patch — Stage 4.5 prompt patch input."""

    user_feedback: str = Field(..., min_length=1, max_length=4000)


class DraftPatchResponse(BaseModel):
    """The Stage 4.5 LLM patch result, with the resulting tag_weights applied."""

    draft_id: int
    profile_name: str
    tag_weight_deltas: dict[str, float]
    required_segments_overrides: dict[str, Any]
    patched_tag_weights: dict[str, float]
    patched_required_segments: dict[str, Any]


# Resolve forward reference: UploadCompleteOut references AssetDetail defined below.
UploadCompleteOut.model_rebuild()
