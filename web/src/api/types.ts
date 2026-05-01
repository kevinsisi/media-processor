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
}

export interface DraftSegmentOut {
  order: number;
  asset_segment_id: number;
  on_timeline_start_ms: number;
  on_timeline_end_ms: number;
  transition: string | null;
}

export interface DraftDetail extends DraftSummary {
  segments: DraftSegmentOut[];
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

export type AnalysisStep = "stt" | "scene" | "motion" | "coverage";

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
  // Public URLs (e.g. "/api/media/thumbnails/12/frame_2.jpg") for the
  // keyframe gallery; empty until ffmpeg has produced the frames.
  thumbnail_urls: string[];
}

export interface ThumbnailUrl {
  index: number;
  url: string;
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
