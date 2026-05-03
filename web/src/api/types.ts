// Mirrors src/media_processor/api/schemas.py (Pydantic models).
// Hand-maintained for now — there's no OpenAPI codegen step yet.

export type ReviewAction = "approve" | "reject" | "repatch" | "download";
export type TargetAspectRatio = "9:16" | "4:5" | "1:1";
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
}

// v0.18 — PATCH /projects/{id}/watermark body. Every field optional so
// the picker can update one slider at a time without echoing the rest.
export interface WatermarkSettingsPatch {
  position?: WatermarkPosition;
  scale?: number;
  opacity?: number;
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

export interface DraftReorderRequest {
  // New permutation of the existing DraftSegment ids (full replacement).
  orders: number[];
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

export type ExportAspect = "9:16" | "4:5" | "1:1";

export interface DraftExportRequest {
  aspect: ExportAspect;
  height: number;
}

export interface DraftExportResponse {
  draft_id: number;
  aspect: string;
  height: number;
  job_id: string;
  output_filename: string;
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
  // -1 = custom_roi, -2 = fixed framing, -3 = no auto-reframe.
  tracked_object_index: number | null;
  has_custom_roi: boolean;
}

export type TrackingMode = "auto" | "object" | "custom" | "fixed" | "none";

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
}

export interface TrackingTargetResponse {
  asset_id: number;
  tracked_object_index: number | null;
  has_custom_roi: boolean;
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
