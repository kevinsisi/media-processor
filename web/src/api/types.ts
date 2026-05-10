// Mirrors src/media_processor/api/schemas.py (Pydantic models).
// Hand-maintained for now — there's no OpenAPI codegen step yet.

export type ReviewAction = "approve" | "reject" | "repatch" | "download";
// v0.29.0 — narrowed from {9:16, 4:5, 1:1} to {9:16, 16:9}. Operators
// shipped only Reels and asked for a horizontal landscape variant
// for YouTube / desktop-feed. Legacy 4:5 / 1:1 projects are migrated
// to 9:16 by alembic 0026 so a stale row never reaches the FE.
export type TargetAspectRatio = "9:16" | "16:9";
export type UploadKind = "video" | "script";
export type UploadStatus = "pending" | "complete" | "aborted";

export interface ProjectSummary {
  id: number;
  name: string;
  client: string | null;
  profile_name: string;
  status: string;
  target_aspect_ratio: string;
  created_at: string;
  asset_count: number;
  latest_draft_version: number | null;
}

export type WatermarkPosition =
  | "top-left"
  | "top-center"
  | "top-right"
  | "middle-left"
  | "middle-center"
  | "middle-right"
  | "bottom-left"
  | "bottom-center"
  | "bottom-right";

// v0.18 — subtitle style customisation. Keep these unions in sync with
// services.video_renderer.SUBTITLE_*_CHOICES and the matching pydantic
// Literal types in api/schemas.py.
export type SubtitleFont =
  | "noto_sans_tc"
  | "noto_sans_tc_bold"
  | "noto_serif_tc";
export type SubtitlePosition = "top" | "middle" | "bottom";
export type SubtitleSize = "small" | "medium" | "large";
export type SubtitleOutlineWidth = "none" | "thin" | "thick";

export interface SubtitleStylePatch {
  subtitle_font?: SubtitleFont;
  subtitle_color?: string;
  subtitle_outline_color?: string;
  subtitle_position?: SubtitlePosition;
  subtitle_size?: SubtitleSize;
  subtitle_outline_width?: SubtitleOutlineWidth;
}

export interface ProjectDetail {
  id: number;
  name: string;
  client: string | null;
  profile_name: string;
  source_dir: string;
  status: string;
  target_aspect_ratio: string;
  created_at: string;
  asset_count: number;
  draft_count: number;
  // M6.4 — populated when the project has an uploaded BGM track. UI can
  // show a "BGM ✓" chip when set; null = no BGM and the bgm render stage
  // no-ops.
  bgm_path: string | null;
  // v0.24.0 — BGM tail-fade duration in seconds. ``0`` = pre-0.24.0
  // hard-cut (music stops cold at the end of the video); positive
  // values fade out over the last N seconds. Default 3.0 server-side.
  bgm_fade_out_sec: number;
  // v0.18 — brand watermark / logo overlay. ``watermark_path`` is the
  // on-disk path; ``watermark_url`` is the public URL (with a cache-bust
  // query so re-uploads are picked up immediately). Layout fields carry
  // their defaults even when no PNG has been uploaded yet.
  watermark_path?: string | null;
  watermark_url?: string | null;
  watermark_position?: WatermarkPosition;
  watermark_scale?: number;
  watermark_opacity?: number;
  // v0.18 — subtitle style. Defaults match the historic burn-in look.
  subtitle_font: SubtitleFont;
  subtitle_color: string;
  subtitle_outline_color: string;
  subtitle_position: SubtitlePosition;
  subtitle_size: SubtitleSize;
  subtitle_outline_width: SubtitleOutlineWidth;
  // v0.21 — auto-edit planner subject filter. When set to one of the 80
  // COCO class names, the planner shrinks each asset's used span to
  // the time range where that class is detected in tracking_json,
  // padded ±0.5 s. ``null`` = no filter (historical default).
  subject_class?: string | null;
  // v0.29.0 — static-crop anchor used when source orientation differs
  // from target orientation (9:16 ↔ 16:9). ``null`` ≡ centre. The FE
  // mounts a CropRegionPicker only when the project has at least one
  // analysed asset whose orientation disagrees with target_aspect_ratio.
  crop_region?: CropRegion | null;
  // v0.30.0 — opt-in AI Smart Camera. Persistent project toggle the
  // FE renders as an experimental-feature checkbox. Default ``false``
  // (the entire feature is opt-in by design; running smart camera
  // costs extra Gemini quota and the resulting moves can surprise
  // operators who wanted a static camera). Mutually exclusive with
  // vidstab > auto-reframe / point-tracking; smart camera wins over
  // emotion zoompan when both could fire.
  smart_camera_enabled?: boolean;
}

// v0.30.0 — body for ``PATCH /projects/{id}/smart-camera``. Single
// boolean — there's no third "inherit" state at the project level;
// the per-render override lives on EditTriggerRequest.
export interface SmartCameraPatch {
  enabled: boolean;
}

// v0.29.0 — fraction-of-source crop anchor. (0.5, 0.5) is centre;
// (0.5, 0.0) anchors at the top of the source; (0.5, 1.0) anchors at
// the bottom. Renderer clamps so the crop window stays inside the
// source.
export interface CropRegion {
  x_norm: number;
  y_norm: number;
}

// v0.29.0 — body for PATCH /projects/{id}/crop-region. Both fields
// null clears the override (revert to centre); both populated saves
// a custom anchor. Mixed (one null, one not) is rejected with 400.
export interface CropRegionPatch {
  x_norm: number | null;
  y_norm: number | null;
}

// v0.21 — PATCH /projects/{id}/subject-class body. ``null`` clears the
// filter; a string must be one of the 80 COCO class names (validated
// server-side).
export interface SubjectClassPatch {
  subject_class: string | null;
}

// v0.21 — GET /projects/{id}/detected-classes — one row per class that
// actually appears in any of this project's assets' tracking_json.
// Sorted server-side by ``total_frames`` descending so the most common
// subject lands first; ``asset_count`` is the number of distinct
// assets the class shows up in.
export interface DetectedClassOut {
  cls_name: string;
  total_frames: number;
  asset_count: number;
}

// v0.18 — PATCH /projects/{id}/watermark body. Every field optional so
// the picker can update one slider at a time without echoing the rest.
export interface WatermarkSettingsPatch {
  position?: WatermarkPosition;
  scale?: number;
  opacity?: number;
}

// v0.21.6 — saved watermark presets that can be applied to any
// project. ``preview_url`` carries a cache-bust query so a re-saved
// preset (same id) shows the new file immediately.
export interface WatermarkPresetOut {
  id: number;
  name: string;
  position: WatermarkPosition;
  scale: number;
  opacity: number;
  created_at: string;
  preview_url: string | null;
}

// POST /watermark-presets body — captures the named preset; the PNG
// file + position / scale / opacity are copied from this project's
// current watermark on the server side.
export interface WatermarkPresetSaveRequest {
  project_id: number;
  name: string;
}

// POST /projects/{id}/watermark/apply-preset body.
export interface WatermarkPresetApplyRequest {
  preset_id: number;
}

export interface ProjectCreate {
  name: string;
  client?: string | null;
  profile_name: string;
  target_aspect_ratio: TargetAspectRatio;
}

export interface ScriptOut {
  project_id: number;
  body: string;
  source_filename: string | null;
  updated_at: string;
}

export interface ScriptUpsert {
  body: string;
  source_filename?: string | null;
}

export interface UploadSessionCreate {
  kind: UploadKind;
  filename: string;
  total_size: number;
  chunk_size: number;
  sha256?: string | null;
}

export interface UploadSessionOut {
  id: string;
  project_id: number;
  kind: string;
  filename: string;
  total_size: number;
  chunk_size: number;
  received_chunks: number[];
  status: string;
}

export interface UploadCompleteOut {
  session: UploadSessionOut;
  asset: AssetDetail | null;
  script: ScriptOut | null;
}

export type CutSourceKind = "scripted" | "improv";

export interface CutPlanSegmentOut {
  order: number;
  asset_id: number;
  asset_start_ms: number;
  asset_end_ms: number;
  source_kind: CutSourceKind;
  reason: string;
  // M6.3 — xfade transition into the next cut. Defaults to "dissolve"
  // for older drafts whose stored cut_plan_json predates the field.
  transition_to_next: string;
}

export interface CutPlanOut {
  schema_version: string;
  target_duration_ms: number;
  target_aspect_ratio: string;
  profile_name: string;
  notes: string;
  used_fallback: boolean;
  fallback_reason: string | null;
  segments: CutPlanSegmentOut[];
}

export type EditStep =
  | "plan"
  | "cut"
  | "stabilize"
  | "concat"
  | "subtitles"
  | "bgm";

export type ClipStylePreset =
  | "fast"
  | "slow"
  | "commercial"
  | "artistic"
  | "custom";

export interface DraftSummary {
  id: number;
  project_id: number;
  profile_name: string;
  version: number;
  status: string;
  output_zip_path: string | null;
  mp4_preview_path: string | null;
  ai_score: number | null;
  created_at: string;
  // M5 — render progress + URLs (filled by the worker as it makes progress).
  progress_steps?: Partial<Record<EditStep, string>> | null;
  mp4_url?: string | null;
  subtitle_url?: string | null;
  // v0.18 — clip-style preset that biased the planner for this draft.
  // Old rows that pre-date the column come back as "custom".
  style_preset?: ClipStylePreset;
}

export interface DraftSegmentOut {
  // M7.1 — needed for the reorder API which takes a permutation of ids.
  id: number;
  order: number;
  asset_segment_id: number | null;
  asset_id: number | null;
  asset_start_ms: number | null;
  asset_end_ms: number | null;
  on_timeline_start_ms: number;
  on_timeline_end_ms: number;
  transition: string | null;
  source_kind: CutSourceKind | null;
  plan_reason: string | null;
  // v0.17 — per-segment audio gain. ``voice_volume`` defaults to 1.0
  // (original gain); ``bgm_volume`` is null = auto-ducking curve.
  voice_volume?: number;
  bgm_volume?: number | null;
}

// v0.17 — per-segment volume PATCH.
export interface SegmentVolumePatch {
  voice_volume?: number;
  bgm_volume?: number | null;
}

export interface SegmentVolumeOut {
  id: number;
  voice_volume: number;
  bgm_volume: number | null;
}

export interface DraftDetail extends DraftSummary {
  segments: DraftSegmentOut[];
  cut_plan?: CutPlanOut | null;
  prompt_feedback?: string | null;
}

export interface EditTriggerRequest {
  force?: boolean;
  // User-configurable render length in seconds. Backend clamps to 10–300;
  // omit to let the orchestrator pick from source duration.
  target_duration_seconds?: number;
  // v0.14.3 — toggle the two-pass vidstab digital stabilization stage.
  // Default true on the backend; the UI exposes a switch so tripod /
  // gimbal projects can opt out to halve render time.
  stabilize?: boolean;
  // v0.14.4 — toggle subtitle burn-in. False ships an mp4 without
  // burned captions (the SRT side-output is also skipped).
  subtitles?: boolean;
  // v0.14.4 — toggle xfade transitions. False uses hard cuts (concat
  // demuxer plain mux, no overlap, no xfade re-encode).
  transitions?: boolean;
  // v0.16 — toggle auto-reframe (YOLO-tracked dynamic crop). Default
  // true on the backend; assets without tracking_json silently fall
  // back to the static centered crop so leaving this on is safe even
  // on partially analyzed projects.
  auto_reframe?: boolean;
  // v0.30.0 — opt-in AI Smart Camera per-run override. ``null`` /
  // omitted = inherit ``Project.smart_camera_enabled``; explicit
  // ``true`` / ``false`` overrides for this single render.
  smart_camera?: boolean | null;
  // v0.18 — clip-style preset. ``custom`` (default) keeps legacy
  // free-form behaviour; the four named presets steer span / transition
  // / BGM hint together so the user gets a coherent rhythm.
  style_preset?: ClipStylePreset;
}

export interface EditTriggerResponse {
  project_id: number;
  draft_id: number;
  job_id: string;
  status: string;
}

// ----- M5.2 — per-version draft comment thread -----

export interface DraftComment {
  id: number;
  draft_id: number;
  author: string;
  body: string;
  created_at: string;
}

export interface DraftCommentCreate {
  author: string;
  body: string;
}

// ----- M7 — manual control types -----

// v0.21.3 — optional override for the four render flags on the
// skip-plan re-render endpoints (reorder + rebuild-subtitles). Each
// field is optional; absent fields fall back to the snapshot stored
// on ``Draft.render_flags_json`` (or all-True for legacy NULL rows).
// FE sends the operator's current ProjectEdit toggle state so a
// legacy draft re-rendered after the user turned transitions off
// honours that choice instead of silently re-enabling them.
export interface RenderFlagsOverride {
  transitions?: boolean;
  stabilize?: boolean;
  subtitles?: boolean;
  auto_reframe?: boolean;
  // v0.30.0 — opt-in AI Smart Camera flag for the skip-plan
  // re-render endpoints. Same priority semantics as the others:
  // body > snapshot > false (legacy default).
  smart_camera?: boolean;
}

export interface DraftReorderRequest {
  // New permutation of the existing DraftSegment ids (full replacement).
  orders: number[];
  render_flags?: RenderFlagsOverride;
}

export interface DraftRebuildSubtitlesRequest {
  render_flags?: RenderFlagsOverride;
}

// ----- v0.20 — timeline editor segment-level mutations -----

export interface DraftSegmentSplitRequest {
  // On-timeline ms; must fall strictly inside the target segment.
  at_ms: number;
}

export interface DraftSegmentPatch {
  // All optional. Asset-time bounds validated server-side against
  // Asset.duration_ms; transition validated against the renderer's
  // whitelist; volumes clamped to [0.0, 1.5].
  asset_start_ms?: number;
  asset_end_ms?: number;
  transition?: string;
  voice_volume?: number;
  // Set to null explicitly to clear the override (= mixer default
  // ducking curve). Omit to leave unchanged.
  bgm_volume?: number | null;
}

export interface SubtitleCueOut {
  idx: number;
  start_ms: number;
  end_ms: number;
  text: string;
  updated_at: string;
}

export interface SubtitleCuePatch {
  text: string;
}

// v0.29.0 — same shrink as TargetAspectRatio. Pre-v0.29 export
// artifacts at 4:5 / 1:1 stay downloadable through the artifacts
// list, but new exports are limited to these two.
export type ExportAspect = "9:16" | "16:9";

export interface DraftExportRequest {
  aspect: ExportAspect;
  height: number;
}

export interface DraftExportResponse {
  export_id: number;
  draft_id: number;
  aspect: string;
  height: number;
  job_id: string;
  output_filename: string;
  status: DraftExportStatus;
  download_url: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export type DraftExportStatus = "queued" | "running" | "done" | "failed";

export interface DraftExportArtifact {
  export_id: number;
  draft_id: number;
  aspect: string;
  height: number;
  status: DraftExportStatus;
  job_id: string | null;
  output_filename: string;
  download_url: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface AssetTagOut {
  tag_type: string;
  tag_name: string;
  confidence: number;
  source_model: string;
  time_ranges_ms: unknown | null;
}

export interface AssetDetail {
  id: number;
  project_id: number;
  file_path: string;
  duration_ms: number;
  resolution: string | null;
  fps: number | null;
  codec: string | null;
  sha256: string;
  thumbnail_path: string | null;
  status: string;
  tags: AssetTagOut[];
  analysis_steps?: Record<string, string> | null;
}

// ----- M4 — transcript / coverage / analyze types -----

export interface TranscriptSegmentOut {
  idx: number;
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface TranscriptSegmentIn {
  start_ms: number;
  end_ms: number;
  text: string;
}

export interface TranscriptOut {
  asset_id: number;
  language: string;
  model: string;
  transcript_text: string;
  segments: TranscriptSegmentOut[];
  edited: boolean;
  created_at: string;
  updated_at: string;
}

export interface TranscriptUpsert {
  segments: TranscriptSegmentIn[];
}

export type CoverageClassification = "scripted" | "improvised";

export interface CoverageMatchOut {
  transcript_idx: number;
  classification: CoverageClassification;
  confidence: number;
  matched_script_excerpt: string;
}

export interface ScriptCoverageOut {
  asset_id: number;
  script_id: number;
  model: string;
  scripted_segment_count: number;
  total_segment_count: number;
  coverage_ratio_by_count: number;
  coverage_ratio_by_duration_ms: number;
  matches: CoverageMatchOut[];
  computed_at: string;
}

export type AnalysisStep =
  | "stt"
  | "scene"
  | "motion"
  | "emotion"
  | "tracking"
  | "coverage";

// Phase 8.1 — face emotion analysis. Mirrors api/schemas.EmotionTagsOut.
export type EmotionTag = "happy" | "surprised" | "serious" | "neutral";

export interface EmotionRangeOut {
  emotion: EmotionTag;
  start_ms: number;
  end_ms: number;
}

export interface EmotionTagsOut {
  dominant: EmotionTag;
  ranges: EmotionRangeOut[];
}

export interface AnalyzeRequest {
  steps?: AnalysisStep[] | null;
  force?: boolean;
}

export interface AnalyzeResponse {
  asset_id: number;
  job_id: string;
  status: string;
  analysis_steps: Record<string, string>;
}

// v0.18 — secondary-language subtitle (Whisper translate). ``lang`` is
// constrained to "en" today because Whisper's translate task always
// emits English; widen this union once additional models land.
export type SecondarySubtitleLang = "en";

export interface TranslateSubtitleRequest {
  lang?: SecondarySubtitleLang;
}

export interface TranslateSubtitleResponse {
  asset_id: number;
  job_id: string;
  lang: string;
}

export interface SecondarySubtitleSummary {
  lang: string;
  segment_count: number;
}

export type MotionType = "pan" | "tilt" | "zoom" | "static" | "handheld";

export interface MotionSegmentOut {
  motion_type: MotionType;
  start_ms: number;
  end_ms: number;
}

export interface SceneTagOut {
  name: string;
  confidence: number;
}

export interface TranscriptSummaryOut {
  segment_count: number;
  edited: boolean;
  updated_at: string;
}

export interface CoverageSummaryOut {
  coverage_ratio_by_count: number;
  coverage_ratio_by_duration_ms: number;
  scripted_segment_count: number;
  total_segment_count: number;
}

export interface AssetAnalysisItem {
  id: number;
  file_path: string;
  filename: string;
  duration_ms: number;
  // v0.26.0 — surface source resolution + on-disk byte size so the
  // analysis-page card can render a one-line spec underneath the
  // filename ("1:23 · 1080×1920 · 45.2 MB"). Both ``null`` when
  // the underlying source isn't queryable (resolution: ffprobe
  // failed at upload; size: file missing on disk).
  resolution?: string | null;
  file_size_bytes?: number | null;
  status: string;
  analysis_steps: Record<string, string> | null;
  transcript_summary: TranscriptSummaryOut | null;
  coverage_summary: CoverageSummaryOut | null;
  scene_tags: SceneTagOut[];
  motion_segments: MotionSegmentOut[];
  // Phase 8.1 — null when the emotion stage hasn't run for this asset.
  emotion_tags?: EmotionTagsOut | null;
  // v0.16 — null when the tracking stage hasn't run; ``frame_count: 0``
  // and ``subject_class: ""`` when YOLO saw no recognised subjects.
  tracking_summary?: TrackingSummaryOut | null;
  // v0.18 — null when no secondary translation has been generated for
  // this asset yet. ``lang`` is the ISO code (e.g. "en"); the chip on
  // the analysis page renders "EN · 24 段" when set.
  secondary_subtitle_summary?: SecondarySubtitleSummary | null;
  // Public URLs (e.g. "/api/media/thumbnails/12/frame_2.jpg") for the
  // keyframe gallery; empty until ffmpeg has produced the frames.
  thumbnail_urls: string[];
}

export interface TrackingSummaryOut {
  subject_class: string;
  confidence: number;
  frame_count: number;
  sampled_frames: number;
}

// v0.17 — per-class object tracks for the picker UI on the analysis page.
export interface TrackingTrackOut {
  object_index: number;
  cls_name: string;
  confidence: number;
  area_score: number;
  frame_count: number;
  // [t_ms, x, y, w, h] tuples downsampled from the full per-frame track.
  sample_frames: number[][];
}

export interface TrackingDetailOut {
  src_w: number;
  src_h: number;
  fps: number;
  sampled_frames: number;
  subject_class: string;
  confidence: number;
  tracks: TrackingTrackOut[];
  // null = auto (dominant track). >= 0 = picked object_index.
  // -1 = custom_roi, -2 = fixed framing, -3 = no auto-reframe,
  // -4 = point_tracking (v0.23).
  tracked_object_index: number | null;
  has_custom_roi: boolean;
  custom_roi_origin?: {
    x: number;
    y: number;
    w: number;
    h: number;
    source_t_ms?: number;
  } | null;
  // v0.23 — surfaces ``Asset.point_tracking_json`` presence so the
  // picker can render a "✓ pixel tracked" indicator without
  // re-fetching the per-frame trace.
  has_point_track?: boolean;
  // v0.23 — verbatim user click that seeded the LK trace. The FE
  // renders a crosshair at this position on the thumbnail. ``null``
  // when no point track has been run.
  // v0.28.0 — during ``status="pending"`` the ``x`` / ``y`` keys are
  // absent (cv2 hasn't resolved them yet); the FE only renders the
  // crosshair when ``has_point_track`` AND ``status==="done"``.
  point_tracking_origin?: {
    x?: number;
    y?: number;
    frame_ms: number;
    norm_x: number;
    norm_y: number;
  } | null;
  // v0.28.0 — async LK pipeline status. ``null`` (pre-0.28 row) /
  // ``"pending"`` (worker enqueued, FE polls) / ``"done"`` (trace
  // ready, render crosshair) / ``"failed"`` (worker raised, see
  // ``point_tracking_error``).
  point_tracking_status?: "pending" | "done" | "failed" | null;
  point_tracking_error?: string | null;
}

export type TrackingMode =
  | "auto"
  | "object"
  | "custom"
  | "point"
  | "fixed"
  | "none";

export interface TrackingTargetRequest {
  mode: TrackingMode;
  object_index?: number | null;
  custom_roi?: {
    x: number;
    y: number;
    w: number;
    h: number;
    source_t_ms?: number;
  } | null;
  // v0.23 — pixel-precise point tracking. Coords are 0..1 normalised
  // so the FE can pass display-space click positions without knowing
  // the asset's native resolution.
  point?: {
    norm_x: number;
    norm_y: number;
    frame_ms: number;
  } | null;
}

export interface TrackingTargetResponse {
  asset_id: number;
  has_point_track?: boolean;
  tracked_object_index: number | null;
  has_custom_roi: boolean;
  custom_roi_origin?: {
    x: number;
    y: number;
    w: number;
    h: number;
    source_t_ms?: number;
  } | null;
  // v0.28.0 — set to ``"pending"`` immediately after a mode=point
  // PATCH so the FE knows to flip into polling mode without waiting
  // for the next ``GET /tracking`` round-trip.
  point_tracking_status?: "pending" | "done" | "failed" | null;
}

export interface ThumbnailUrl {
  index: number;
  url: string;
}

// v0.15 — AI BGM generation + curated music library.
export interface MusicSuggestion {
  description: string;
  used_fallback: boolean;
}

export interface BgmGenerationStatus {
  job_id: number | null;
  status: string | null;
  prompt: string | null;
  output_url: string | null;
  error: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface MusicLibraryItem {
  name: string;
  style: string | null;
  duration_s: number | null;
  url: string;
  size_bytes: number;
}

export interface MusicLibraryResponse {
  items: MusicLibraryItem[];
}

export interface AssetThumbnailsOut {
  asset_id: number;
  count: number;
  thumbnails: ThumbnailUrl[];
}

export interface ProjectAnalysisOut {
  project: ProjectDetail;
  has_script: boolean;
  assets: AssetAnalysisItem[];
  // M5 — surface the latest render so the analysis page can show
  // "開始剪輯" / "預覽剪輯" without an extra round-trip.
  latest_draft?: DraftSummary | null;
}

export interface ReviewCreate {
  draft_id: number;
  action: ReviewAction;
  prompt_feedback?: string | null;
  reviewer?: string;
}

export interface ReviewOut {
  id: number;
  draft_id: number;
  reviewer: string;
  action: string;
  prompt_feedback: string | null;
  reviewed_at: string;
}

// ----- Settings — runtime LLM key pool -----

export type KeyPoolSource = "db" | "env" | "none";

export interface KeyPoolOut {
  count: number;
  source: KeyPoolSource;
  masked_suffixes: string[];
}

export interface SettingsOut {
  llm_model: string;
  llm_timeout_s: number;
  llm_api_keys: KeyPoolOut;
}

export interface LLMKeysUpdateIn {
  raw: string;
  replace?: boolean;
}

export interface LLMKeysUpdateOut {
  stored_count: number;
  accepted_count: number;
  rejected_count: number;
}

export interface SyncFromManagerIn {
  url?: string;
  trusted_only?: boolean;
  replace?: boolean;
}

export interface SyncFromManagerOut {
  fetched: number;
  imported: number;
  skipped: number;
  stored_count: number;
}

// v0.25.0 / v0.27.0 — RQ queue inspector. The API returns every
// currently running job plus the ordered queued list across analysis /
// editing / bgm. The position field is queue-order metadata for the UI.
export type QueueName = "analysis" | "editing" | "bgm";
export type QueueJobState = "running" | "queued";

export interface QueueJobItem {
  job_id: string;
  queue: QueueName;
  // Operator-facing kind label. The full func_name is mapped server-side:
  // analyze / translate / render / export / bgm / unknown.
  kind: string;
  state: QueueJobState;
  // 0-indexed position in the queued list. ``null`` for the running item.
  position: number | null;
  enqueued_at: string | null;
  started_at: string | null;
  elapsed_s: number | null;
  // Best-effort entity context. Resolved server-side — asset-bound
  // jobs (analyze / translate) and draft-bound jobs (export) get
  // their project_id backfilled from the DB so the FE can render
  // "X 的 Y" without an extra round-trip.
  project_id: number | null;
  project_name: string | null;
  asset_id: number | null;
  draft_id: number | null;
  bgm_job_id: number | null;
}

export interface QueueStatusOut {
  // v0.27.0 — multi-worker: up to 5 concurrent running jobs (1
  // analysis + 3 editing + 1 bgm). The list shape lets the FE render
  // every live job in parallel.
  running: QueueJobItem[];
  queued: QueueJobItem[];
}


// v0.27.1 — one active-draft reference returned alongside a delete
// outcome. Lets the FE render "v3, v5 still using this — really
// delete?" without making a separate fetch for draft state.
export interface AffectedDraftOut {
  draft_id: number;
  version: number;
  status: string;
}

// v0.26.0 / v0.27.1 — batch asset delete. The endpoint returns per-
// row outcomes so the FE can list which assets refused (e.g. still
// used by an active draft) instead of hiding them behind a single
// "請重試" blanket. ``reason`` is null when the row was deleted;
// otherwise a terse human-readable string.
//
// v0.27.1 fields:
//   * ``affected_drafts`` — when non-empty, the row was either skipped
//     (``deleted=false``) because the user must confirm a force-delete,
//     or already force-deleted (``deleted=true``) and the list is
//     echoed back so the FE can show which versions just got
//     invalidated.
//   * ``invalidated_versions`` — subset of the affected versions
//     whose drafts were flipped to ``failed`` because the segment
//     wipe left them with no segments. Always empty on
//     ``deleted=false``.
export interface AssetBatchDeleteResultItem {
  asset_id: number;
  deleted: boolean;
  affected_drafts: AffectedDraftOut[];
  invalidated_versions: number[];
  reason: string | null;
}

export interface AssetBatchDeleteRequest {
  asset_ids: number[];
}

export interface AssetBatchDeleteOut {
  deleted_count: number;
  // Total non-deleted rows. v0.27.1 splits this into:
  //   * needs_force_count — rows with affected_drafts that the FE
  //     can re-issue with ?force=true
  //   * error_count — rows blocked for other reasons (not in this
  //     project, not found, internal error)
  blocked_count: number;
  needs_force_count: number;
  error_count: number;
  results: AssetBatchDeleteResultItem[];
}

// v0.27.1 — single-asset delete now returns 200 with this body
// instead of 204 No Content. ``deleted=false`` with non-empty
// ``affected_drafts`` is the FE's signal to confirm the destructive
// action and retry the same request with ``?force=true``.
export interface AssetDeleteOut {
  asset_id: number;
  deleted: boolean;
  affected_drafts: AffectedDraftOut[];
  invalidated_versions: number[];
}
