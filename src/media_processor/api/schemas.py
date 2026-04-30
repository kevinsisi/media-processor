"""Pydantic response/request schemas for the M2 API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from media_processor.models.enums import REVIEW_ACTION_VALUES

ReviewActionLiteral = Literal["approve", "reject", "repatch", "download"]


class ProjectSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    client: str | None
    profile_name: str
    status: str
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
    created_at: datetime
    asset_count: int
    draft_count: int


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
