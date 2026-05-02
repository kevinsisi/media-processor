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
    asset: AssetDetail | None = None
    script: ScriptOut | None = None


class CutPlanSegmentOut(BaseModel):
    """One segment from the stored Gemini cut plan."""

    order: int
    asset_id: int
    asset_start_ms: int
    asset_end_ms: int
    source_kind: Literal["scripted", "improv"]
    reason: str
    # M6.3 — xfade transition into the next cut. Default coerce keeps
    # older drafts (pre-M6) renderable since their stored blobs lack
    # this field.
    transition_to_next: str = "dissolve"


class CutPlanOut(BaseModel):
    """Mirror of edit_planner.serialise_plan output."""

    schema_version: str
    target_duration_ms: int
    target_aspect_ratio: str
    profile_name: str
    notes: str
    used_fallback: bool
    fallback_reason: str | None
    segments: list[CutPlanSegmentOut]


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
    progress_steps: dict[str, str] | None = None
    mp4_url: str | None = None
    subtitle_url: str | None = None


class DraftSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # M7.1 — surface the row id so the timeline-reorder API can take a
    # permutation of the existing segment ids without the UI guessing.
    id: int
    order: int
    asset_segment_id: int | None = None
    asset_id: int | None = None
    asset_start_ms: int | None = None
    asset_end_ms: int | None = None
    on_timeline_start_ms: int
    on_timeline_end_ms: int
    transition: str | None
    source_kind: str | None = None
    plan_reason: str | None = None


class DraftDetail(DraftSummary):
    segments: list[DraftSegmentOut]
    cut_plan: CutPlanOut | None = None
    prompt_feedback: str | None = None


class EditTriggerRequest(BaseModel):
    """Body for POST /projects/{id}/edit — every field optional."""

    force: bool = False
    # User-configurable target duration in seconds. The web client offers
    # quick-pick buttons (30/60/90/120) plus a free-form input; omit to
    # let the orchestrator compute a duration from the source material.
    # Bounds match the M5 design: 10 s floor (still a usable IG/TikTok
    # short) and 300 s (5 min) ceiling so a single Gemini call stays
    # within the prompt + response budget.
    target_duration_seconds: int | None = Field(default=None, ge=10, le=300)


class EditTriggerResponse(BaseModel):
    """Returned by POST /projects/{id}/edit (202 Accepted)."""

    project_id: int
    draft_id: int
    job_id: str
    status: str


# ---------- M5.2 — per-version comment thread ----------


class DraftCommentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    draft_id: int
    author: str
    body: str
    created_at: datetime


class DraftCommentCreate(BaseModel):
    """Body for POST /drafts/{id}/comments. ``author`` is captured from the
    UI text field; we don't have auth yet so trust the client."""

    author: str = Field(..., min_length=1, max_length=64)
    body: str = Field(..., min_length=1, max_length=4000)


# ---------- M7 — manual control schemas ----------


class DraftReorderRequest(BaseModel):
    """Body for PATCH /drafts/{id}/order — full new order as a permutation
    of the existing DraftSegment ids."""

    orders: list[int] = Field(..., min_length=1, max_length=200)


class SubtitleCueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    idx: int
    start_ms: int
    end_ms: int
    text: str
    updated_at: datetime


class SubtitleCuePatch(BaseModel):
    """Body for PATCH /drafts/{id}/subtitles/{idx}. Timing is immutable —
    we only let the user fix the text."""

    text: str = Field(..., min_length=1, max_length=400)


class DraftExportRequest(BaseModel):
    """Body for POST /drafts/{id}/export."""

    aspect: Literal["9:16", "4:5", "1:1"]
    height: int = Field(..., ge=480, le=2160)


class DraftExportResponse(BaseModel):
    """Returned by POST /drafts/{id}/export."""

    draft_id: int
    aspect: str
    height: int
    job_id: str
    output_filename: str


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


# Phase 8.1 — face emotion analysis output. ``ranges`` is the merged
# per-class spans returned by ``services.emotion``; ``dominant`` is the
# verdict the planner / renderer act on.
EmotionTagLiteral = Literal["happy", "surprised", "serious", "neutral"]


class EmotionRangeOut(BaseModel):
    emotion: EmotionTagLiteral
    start_ms: int
    end_ms: int


class EmotionTagsOut(BaseModel):
    dominant: EmotionTagLiteral
    ranges: list[EmotionRangeOut]


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
    # Phase 8.1 — null when the emotion stage hasn't run for this asset.
    emotion_tags: EmotionTagsOut | None = None
    # Public URLs for the keyframe gallery; empty list when frames have not
    # been generated yet (the UI shows a placeholder).
    thumbnail_urls: list[str]


class ProjectAnalysisOut(BaseModel):
    """Returned by GET /projects/{id}/assets — drives the polling page."""

    project: ProjectDetail
    has_script: bool
    assets: list[AssetAnalysisItem]
    # M5 — surface the latest draft's render state so the analysis page
    # can show 開始剪輯 / 預覽剪輯 without an extra round-trip.
    latest_draft: DraftSummary | None = None


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


# ----- M4.6 — thumbnail gallery schemas -----


class ThumbnailUrl(BaseModel):
    index: int
    url: str


class AssetThumbnailsOut(BaseModel):
    """Returned by GET /assets/{id}/thumbnails — list of generated frames."""

    asset_id: int
    count: int
    thumbnails: list[ThumbnailUrl]


# Resolve forward reference: UploadCompleteOut references AssetDetail defined below.
UploadCompleteOut.model_rebuild()
