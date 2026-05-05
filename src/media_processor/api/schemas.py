"""Pydantic response/request schemas for the M2 API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from media_processor.models.enums import REVIEW_ACTION_VALUES
from media_processor.services.object_tracking import COCO80_CLASSES

ReviewActionLiteral = Literal["approve", "reject", "repatch", "download"]
TargetAspectRatioLiteral = Literal["9:16", "4:5", "1:1"]
UploadKindLiteral = Literal["video", "script"]
# v0.18 — 9-grid watermark anchor. Mirrors video_renderer._WATERMARK_POSITIONS.
WatermarkPositionLiteral = Literal[
    "top-left",
    "top-center",
    "top-right",
    "middle-left",
    "middle-center",
    "middle-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
]

# v0.18 — subtitle style customisation. Keep these literal lists in sync
# with services.video_renderer.SUBTITLE_FONT_CHOICES /
# SUBTITLE_SIZE_CHOICES / SUBTITLE_POSITION_CHOICES /
# SUBTITLE_OUTLINE_WIDTH_CHOICES so a stale literal can't quietly accept
# a value the renderer doesn't know how to apply.
SubtitleFontLiteral = Literal[
    "noto_sans_tc",
    "noto_sans_tc_bold",
    "noto_serif_tc",
]
SubtitlePositionLiteral = Literal["top", "middle", "bottom"]
SubtitleSizeLiteral = Literal["small", "medium", "large"]
SubtitleOutlineWidthLiteral = Literal["none", "thin", "thick"]
# Hex colours like "#ffffff" or shorthand "#fff". Rejected with 422 when
# the format doesn't match.
SUBTITLE_COLOR_PATTERN = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"

# v0.18 — clip-style preset that biases planner span / transition / BGM hint.
ClipStylePresetLiteral = Literal["fast", "slow", "commercial", "artistic", "custom"]
DraftExportStatusLiteral = Literal["queued", "running", "done", "failed"]


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
    # M6.4 — populated when the project has an uploaded BGM track.
    bgm_path: str | None = None
    # v0.24.0 — tail-fade duration (seconds) for the BGM mix. ``0`` =
    # historical hard-cut. Default 3.0 s, capped at 10 s server-side
    # (FE slider exposes 0..5 s for the common range).
    bgm_fade_out_sec: float = 3.0
    # v0.18 — watermark / logo overlay settings. ``watermark_path`` is
    # null when the user hasn't uploaded a PNG yet; the layout fields
    # carry their defaults so the UI can render the picker pre-filled.
    watermark_path: str | None = None
    watermark_url: str | None = None
    watermark_position: WatermarkPositionLiteral = "bottom-right"
    watermark_scale: float = 0.10
    watermark_opacity: float = 1.0
    # v0.18 — subtitle style settings. Defaults match the historic
    # white-on-black/Noto Sans CJK TC bottom-anchored look so older
    # clients can ignore these fields without behavioural drift.
    subtitle_font: SubtitleFontLiteral = "noto_sans_tc"
    subtitle_color: str = "#ffffff"
    subtitle_outline_color: str = "#000000"
    subtitle_position: SubtitlePositionLiteral = "bottom"
    subtitle_size: SubtitleSizeLiteral = "medium"
    subtitle_outline_width: SubtitleOutlineWidthLiteral = "thin"
    # v0.21 — optional subject-class filter for the auto-edit planner.
    # ``None`` means "no filter, use every asset at full duration"
    # (historical default). Otherwise one of the 80 COCO class names.
    subject_class: str | None = None


class WatermarkSettingsPatch(BaseModel):
    """PATCH /projects/{id}/watermark — body. Every field optional.

    Bounds match ``services.video_renderer.apply_watermark`` validation:
    scale capped to 0.5 so the logo can't dominate the frame, opacity
    capped to 1.0 (fully opaque) and floored at 0.0 (invisible).
    """

    position: WatermarkPositionLiteral | None = None
    scale: float | None = Field(default=None, ge=0.02, le=0.5)
    opacity: float | None = Field(default=None, ge=0.0, le=1.0)


class AssetBatchDeleteRequest(BaseModel):
    """v0.26.0 — body for ``DELETE /projects/{id}/assets/batch``.

    ``asset_ids`` is the list of Asset.id rows to wipe. Each is
    deleted independently — a per-asset failure (e.g. one is still
    referenced by an active draft) doesn't block the rest. The
    endpoint returns a summary so the UI can list which ones
    refused.

    v0.27.1: a row with ``deleted=False`` AND non-empty
    ``affected_drafts`` is the FE's signal to confirm and retry the
    batch with ``?force=true``. Pre-0.27.1 such rows came back with
    a 409-style ``reason`` and the operator had to manually reject
    each draft first.
    """

    asset_ids: list[int] = Field(..., min_length=1)


class AffectedDraftOut(BaseModel):
    """v0.27.1 — one active-draft reference to an asset.

    Surfaced under ``affected_drafts`` on every delete-result shape
    so the FE can render "v3, v5 still using this — really delete?"
    """

    draft_id: int
    version: int
    status: str


class AssetBatchDeleteResultItem(BaseModel):
    """One row in the batch-delete summary response."""

    asset_id: int
    deleted: bool
    # v0.27.1 — populated when active drafts referenced this asset.
    # On force=False that means deletion was skipped; on force=True
    # the same list is echoed back along with ``invalidated_versions``
    # so the FE can show "v3 was marked failed".
    affected_drafts: list[AffectedDraftOut] = Field(default_factory=list)
    invalidated_versions: list[int] = Field(default_factory=list)
    reason: str | None = None  # populated when ``deleted=False``


class AssetBatchDeleteOut(BaseModel):
    """v0.26.0 — response for ``DELETE /projects/{id}/assets/batch``.

    v0.27.1 splits ``blocked_count`` into:
      * rows whose deletion was skipped because of active drafts
        (``needs_force_count``) — the FE can prompt the user and
        retry the same batch with ``?force=true``;
      * rows that failed for other reasons (``error_count``) — these
        are surfaced verbatim and not auto-retryable.
    """

    deleted_count: int
    blocked_count: int  # = needs_force_count + error_count, kept for back-compat
    needs_force_count: int = 0
    error_count: int = 0
    results: list[AssetBatchDeleteResultItem]


class AssetDeleteOut(BaseModel):
    """v0.27.1 — response for ``DELETE /assets/{id}``.

    Pre-0.27.1 the endpoint returned 204 No Content on success and
    409 with a plain string detail when an active draft blocked the
    delete. We now always return 200 with this body so the FE can
    distinguish "delete succeeded" from "needs the operator to
    confirm with force=true" without parsing error strings.
    """

    asset_id: int
    deleted: bool
    affected_drafts: list[AffectedDraftOut] = Field(default_factory=list)
    invalidated_versions: list[int] = Field(default_factory=list)


class BgmFadeOutPatch(BaseModel):
    """v0.24.0 — body for PATCH /projects/{id}/bgm-fade-out.

    ``fade_out_sec`` is the tail-fade duration on the BGM mix. ``0``
    keeps the pre-0.24.0 hard-cut behaviour; positive values append
    ``afade=t=out`` so the music tapers into silence over the last
    N seconds. The FE slider exposes 0..5 s; the server allows up
    to 10 s as a safety belt.
    """

    fade_out_sec: float = Field(..., ge=0.0, le=10.0)


class WatermarkPresetSaveRequest(BaseModel):
    """v0.21.6 — POST /watermark-presets body.

    Captures the named preset; the PNG file + position / scale /
    opacity are copied from ``project_id``'s current watermark on
    the server side. The endpoint refuses with 400 when the project
    has no watermark file set yet.
    """

    project_id: int
    name: str = Field(..., min_length=1, max_length=255)


class WatermarkPresetOut(BaseModel):
    """v0.21.6 — one row in GET /watermark-presets."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    position: WatermarkPositionLiteral
    scale: float
    opacity: float
    created_at: datetime
    # Public URL the preset thumbnail / preview can render against.
    preview_url: str | None = None


class WatermarkPresetApplyRequest(BaseModel):
    """v0.21.6 — POST /projects/{id}/watermark/apply-preset body.

    Copies the preset's PNG into the project's watermark slot and
    overwrites the four ``Project.watermark_*`` columns to match.
    """

    preset_id: int


class SubtitleStylePatch(BaseModel):
    """Body for PATCH /projects/{id}/subtitle-style.

    Every field is optional — the user can tweak one knob at a time and
    the others stay at whatever the project already has. Colours must be
    a 3- or 6-digit hex string with a leading ``#``.
    """

    subtitle_font: SubtitleFontLiteral | None = None
    subtitle_color: str | None = Field(default=None, pattern=SUBTITLE_COLOR_PATTERN)
    subtitle_outline_color: str | None = Field(default=None, pattern=SUBTITLE_COLOR_PATTERN)
    subtitle_position: SubtitlePositionLiteral | None = None
    subtitle_size: SubtitleSizeLiteral | None = None
    subtitle_outline_width: SubtitleOutlineWidthLiteral | None = None


class SubjectClassPatch(BaseModel):
    """Body for PATCH /projects/{id}/subject-class.

    ``subject_class=None`` clears the filter (planner uses every asset
    at full duration); a non-null value must be one of the 80 COCO
    class names so the renderer's ``tracking_json`` lookup actually
    matches something.
    """

    subject_class: str | None = Field(default=None, max_length=64)

    @field_validator("subject_class")
    @classmethod
    def _validate_subject_class(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if value not in COCO80_CLASSES:
            raise ValueError(
                f"subject_class must be one of the 80 COCO classes; got {value!r}"
            )
        return value


class DetectedClassOut(BaseModel):
    """v0.21 — one detected class summary across a project's assets.

    Returned by GET /projects/{id}/detected-classes, sorted by
    ``total_frames`` descending so the picker can render the most
    common class first as the natural default.
    """

    cls_name: str
    total_frames: int
    asset_count: int


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
    # v0.18 — preset that biased the planner for this draft. Default
    # ``custom`` keeps legacy behaviour for old rows that pre-date the
    # column.
    style_preset: ClipStylePresetLiteral = "custom"


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
    # v0.17 — per-segment audio gain. ``voice_volume`` defaults to 1.0
    # (original gain); ``bgm_volume`` is null = auto-ducking curve.
    voice_volume: float = 1.0
    bgm_volume: float | None = None


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
    # v0.14.3 — toggle the two-pass vidstab digital stabilization stage.
    # Default is on because phone footage almost always benefits; the
    # frontend exposes a switch so tripod / gimbal projects can opt out
    # to halve render time.
    stabilize: bool = True
    # v0.14.4 — toggle subtitle burn-in. When false the renderer skips
    # the drawtext stage entirely; the mp4 is delivered without burned
    # captions (the SRT is also skipped so the file size stays lean).
    subtitles: bool = True
    # v0.14.4 — toggle xfade transitions between cuts. When false the
    # renderer falls back to the concat-demuxer plain mux (hard cuts,
    # no overlap). Useful for tight news-style edits where xfade
    # softens the cut energy too much.
    # v0.24.0 — default flipped to ``False``. Operator feedback said
    # "every fresh project ships with transitions on and the first
    # thing I do is turn them off"; the default now matches that
    # workflow. Style presets that explicitly want transitions
    # (``slow`` / ``artistic`` / ``commercial``) can still re-enable
    # via the trigger panel.
    transitions: bool = False
    # v0.16 — toggle auto-reframe (YOLO-tracked dynamic crop). Default
    # on: when an asset has tracking_json the renderer drives a
    # Kalman-smoothed crop that keeps the subject centered in the
    # output aspect. Assets without tracking data quietly fall back
    # to the static centered crop.
    auto_reframe: bool = True
    # v0.18 — clip-style preset that biases planner span / transition /
    # BGM hint. ``custom`` keeps the legacy free-form behaviour; the
    # four named presets steer the model toward a coherent rhythm.
    style_preset: ClipStylePresetLiteral = "custom"


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


class RenderFlagsOverride(BaseModel):
    """v0.21.3 — FE-supplied override for the four render flags
    (transitions / stabilize / subtitles / auto_reframe) on the
    skip-plan re-render endpoints. Each field is optional; absent
    fields fall back to what's stored on ``Draft.render_flags_json``
    (or the all-True legacy default when that's NULL too).

    Used so a user who toggles transitions off in ProjectEdit and
    then reorders the timeline gets a hard-cut re-render even when
    the draft predates ``Draft.render_flags_json`` (legacy NULL row).
    The endpoint also writes the resolved flag set back to the Draft
    so subsequent re-renders stay consistent without the FE having
    to re-send.
    """

    transitions: bool | None = None
    stabilize: bool | None = None
    subtitles: bool | None = None
    auto_reframe: bool | None = None


class DraftReorderRequest(BaseModel):
    """Body for PATCH /drafts/{id}/order — full new order as a permutation
    of the existing DraftSegment ids."""

    orders: list[int] = Field(..., min_length=1, max_length=200)
    render_flags: RenderFlagsOverride | None = None


class DraftRebuildSubtitlesRequest(BaseModel):
    """Body for POST /drafts/{id}/rebuild-subtitles — optional render
    flag overrides for the same reasons as DraftReorderRequest. Body
    itself is optional on the endpoint to keep older clients (which
    posted with no body) working."""

    render_flags: RenderFlagsOverride | None = None


# ---------- v0.20 — timeline editor segment-level mutations ----------


class DraftSegmentSplitRequest(BaseModel):
    """Body for POST /drafts/{id}/segments/{seg_id}/split. ``at_ms`` is an
    on-timeline offset (the same coordinate space the playhead uses) and
    must fall strictly inside the segment's
    ``[on_timeline_start_ms, on_timeline_end_ms)`` range — the endpoint
    rejects splits at the exact edges to avoid zero-length halves."""

    at_ms: int = Field(..., ge=0)


class DraftSegmentPatch(BaseModel):
    """Body for PATCH /drafts/{id}/segments/{seg_id}. Every field is
    optional; only present fields are written. Bounds are validated
    server-side against ``Asset.duration_ms`` (asset-time fields) and
    against the renderer's known transition / volume ranges."""

    asset_start_ms: int | None = Field(default=None, ge=0)
    asset_end_ms: int | None = Field(default=None, ge=1)
    transition: str | None = Field(default=None, min_length=1, max_length=64)
    voice_volume: float | None = Field(default=None, ge=0.0, le=1.5)
    # bgm_volume can be set to ``None`` to clear the override (= use the
    # mixer's default ducking curve) — distinguish "not provided" from
    # "explicitly null" via ``model_fields_set`` on the parsed model.
    bgm_volume: float | None = Field(default=None, ge=0.0, le=1.5)


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

    export_id: int
    draft_id: int
    aspect: str
    height: int
    job_id: str
    output_filename: str
    status: DraftExportStatusLiteral
    download_url: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class DraftExportOut(BaseModel):
    """One durable derivative export artifact for a draft."""

    export_id: int
    draft_id: int
    aspect: str
    height: int
    status: DraftExportStatusLiteral
    job_id: str | None = None
    output_filename: str
    download_url: str | None = None
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


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

    steps: (
        list[Literal["stt", "scene", "motion", "emotion", "tracking", "coverage"]] | None
    ) = None
    force: bool = False


class AnalyzeResponse(BaseModel):
    """Returned by POST /assets/{id}/analyze (202 Accepted)."""

    asset_id: int
    job_id: str
    status: str
    analysis_steps: dict[str, str]


# v0.18 — secondary-language subtitle (Whisper translate).
SecondarySubtitleLangLiteral = Literal["en"]


class TranslateSubtitleRequest(BaseModel):
    """Body for POST /assets/{id}/translate-subtitle.

    ``lang`` is currently constrained to ``"en"`` because Whisper's
    translate task always outputs English. Schema literal future-proofs
    the API for additional model variants without breaking the contract.
    """

    lang: SecondarySubtitleLangLiteral = "en"


class TranslateSubtitleResponse(BaseModel):
    """Returned by POST /assets/{id}/translate-subtitle (202 Accepted)."""

    asset_id: int
    job_id: str
    lang: str


class SecondarySubtitleSummaryOut(BaseModel):
    """Compact secondary-subtitle info embedded in the analysis-page list."""

    lang: str
    segment_count: int


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


# v0.16 — YOLO object-tracking summary surfaced on the analysis page.
# ``frame_count`` is 0 + ``subject_class=""`` for assets where the
# tracking step ran but YOLO saw no recognised subjects (legitimate
# b-roll outcome). ``confidence`` is the mean across kept frames.
class TrackingSummaryOut(BaseModel):
    subject_class: str
    confidence: float
    frame_count: int
    sampled_frames: int


# v0.17 — one entry per detected object class (`tracks` in tracking_json).
# The analysis page renders these as bbox overlays + labels so the user
# can pick which one to follow.
class TrackingTrackOut(BaseModel):
    object_index: int
    cls_name: str
    confidence: float
    area_score: float
    frame_count: int
    # Sample bboxes (downsampled to keep the JSON small for the polling
    # endpoint). Each is the [t_ms, x, y, w, h] of one YOLO detection.
    sample_frames: list[list[int]]


class TrackingDetailOut(BaseModel):
    """v0.17 — full tracking data needed by the picker UI.

    Source dimensions come from the tracking blob (matches the YOLO
    input frame size — ffprobe might report different anamorphic sizes,
    but for tracking purposes we use what YOLO saw).
    """

    src_w: int
    src_h: int
    fps: float
    sampled_frames: int
    subject_class: str
    confidence: float
    tracks: list[TrackingTrackOut]
    # Currently active mode. ``None`` (== auto) means "follow the
    # dominant track"; ``-1`` means custom_roi; ``-2``/``-3`` disable
    # auto-reframe; ``-4`` means point_tracking (v0.23).
    tracked_object_index: int | None = None
    has_custom_roi: bool = False
    # v0.23 — surfaces ``Asset.point_tracking_json`` presence so the
    # FE can render a "this asset has an LK pixel-precise track"
    # indicator on the picker. The full per-frame trace lives in DB
    # only — the picker just needs the origin click + a yes/no.
    has_point_track: bool = False
    # v0.23 — verbatim user click that seeded the LK trace. Shape:
    # ``{x: int, y: int, frame_ms: int, norm_x: float, norm_y: float}``
    # so the FE can render a crosshair at the original click position
    # on any thumbnail size. ``None`` when no point track has been run.
    # v0.28.0 — during ``status="pending"`` the ``x`` / ``y`` keys are
    # absent (cv2 hasn't resolved them yet); the FE only renders the
    # crosshair when ``has_point_track`` AND ``status="done"``.
    point_tracking_origin: dict[str, Any] | None = None
    # v0.28.0 — async LK pipeline status. ``None`` (pre-0.28) /
    # ``"pending"`` / ``"done"`` / ``"failed"``. The FE flips into
    # polling mode on ``pending``, renders the crosshair on
    # ``done``, and surfaces ``point_tracking_error`` on ``failed``.
    # ``None`` is treated identically to ``done`` for renderer
    # purposes — pre-0.28 rows that already have a trace remain valid.
    point_tracking_status: str | None = None
    point_tracking_error: str | None = None


class TrackingTargetRequest(BaseModel):
    """PATCH /assets/{id}/tracking-target — body.

    ``mode`` picks the kind of target.

    * ``object_index`` required when ``mode == "object"``
    * ``custom_roi`` required when ``mode == "custom"`` — shape
      ``{x, y, w, h, source_t_ms?}``
    * ``point`` required when ``mode == "point"`` — shape
      ``{norm_x, norm_y, frame_ms}``; ``norm_x`` / ``norm_y`` are
      0..1 normalised so the FE can send display-space coords without
      knowing the asset's native resolution.

    Other modes ignore those fields.
    """

    mode: Literal["auto", "object", "custom", "point", "fixed", "none"]
    object_index: int | None = Field(default=None, ge=0)
    custom_roi: dict[str, Any] | None = None
    point: dict[str, Any] | None = None


class TrackingTargetResponse(BaseModel):
    asset_id: int
    tracked_object_index: int | None
    has_custom_roi: bool
    has_point_track: bool = False
    # v0.28.0 — surfaces the async-LK pipeline state so the FE knows
    # whether to enter polling mode immediately after the PATCH
    # response. ``"pending"`` on a fresh ``mode=point`` PATCH;
    # ``"done"`` after the worker finishes; ``"failed"`` if the
    # worker raised; ``None`` for non-point modes or pre-0.28 rows.
    point_tracking_status: str | None = None


# v0.17 — per-DraftSegment audio gain.
class SegmentVolumePatch(BaseModel):
    """PATCH /drafts/{id}/segments/{seg_id}/volume — body.

    ``voice_volume`` is bounded to 0.0–1.5 (1.0 = original gain). The
    backend clamps anyway; the upper bound stops the user accidentally
    asking for inaudible distortion. ``bgm_volume`` follows the same
    range; ``None`` (or omitted) keeps the auto-duck curve.
    """

    voice_volume: float | None = Field(default=None, ge=0.0, le=1.5)
    bgm_volume: float | None = Field(default=None, ge=0.0, le=1.5)


class SegmentVolumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    voice_volume: float
    bgm_volume: float | None


class AssetAnalysisItem(BaseModel):
    """One row for the project-analysis page polling list."""

    id: int
    file_path: str
    filename: str
    duration_ms: int
    # v0.26.0 — surface the source resolution (already stored on
    # ``Asset.resolution`` from the upload-time ffprobe) and the
    # on-disk file size (statted server-side at request time so we
    # don't carry a stale cached value if the file is moved or
    # truncated). ``None`` for either when the underlying source
    # isn't available yet (resolution: ffprobe failed at upload;
    # size: file missing on disk).
    resolution: str | None = None
    file_size_bytes: int | None = None
    status: str
    analysis_steps: dict[str, str] | None
    transcript_summary: TranscriptSummaryOut | None
    coverage_summary: CoverageSummaryOut | None
    scene_tags: list[SceneTagOut]
    motion_segments: list[MotionSegmentOut]
    # Phase 8.1 — null when the emotion stage hasn't run for this asset.
    emotion_tags: EmotionTagsOut | None = None
    # v0.16 — null when the tracking stage hasn't run for this asset.
    tracking_summary: TrackingSummaryOut | None = None
    # v0.18 — null when no secondary translation has been generated yet.
    secondary_subtitle_summary: SecondarySubtitleSummaryOut | None = None
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


# ----- v0.15 — AI BGM generation + music library -----


class MusicSuggestionOut(BaseModel):
    """GET /projects/{id}/music-suggestion — Gemini-generated description.

    ``description`` is the prefilled textarea content; ``used_fallback``
    is true when the canned default fired because Gemini was unavailable
    or all keys quota-exhausted (so the UI can show a small note).
    """

    description: str
    used_fallback: bool = False


class GenerateBgmRequest(BaseModel):
    """POST /projects/{id}/generate-bgm — body."""

    prompt: str = Field(..., min_length=1, max_length=2000)


class BgmGenerationStatusOut(BaseModel):
    """GET /projects/{id}/bgm-status — latest job for the project.

    ``status`` is one of ``pending`` / ``running`` / ``done`` /
    ``failed:{reason}``. ``output_url`` is null until status==done; UI
    can use it as the audio preview src.
    """

    job_id: int | None = None
    status: str | None = None
    prompt: str | None = None
    output_url: str | None = None
    error: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class MusicLibraryItem(BaseModel):
    """One entry in GET /music-library."""

    name: str  # filename without extension; doubles as the display name
    style: str | None = None  # parsed from a leading "[style] " prefix if present
    duration_s: float | None = None
    url: str  # public path mounted at /api/media/bgm/_library/...
    size_bytes: int


class MusicLibraryOut(BaseModel):
    items: list[MusicLibraryItem]


class SelectLibraryBgmRequest(BaseModel):
    """POST /projects/{id}/bgm/select-library — body."""

    name: str = Field(..., min_length=1, max_length=256)


# Resolve forward reference: UploadCompleteOut references AssetDetail defined below.
UploadCompleteOut.model_rebuild()
