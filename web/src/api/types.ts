// Mirrors src/media_processor/api/schemas.py (Pydantic models).
// Hand-maintained for now — there's no OpenAPI codegen step yet.

export type ReviewAction = "approve" | "reject" | "repatch" | "download";

export interface ProjectSummary {
  id: number;
  name: string;
  client: string | null;
  profile_name: string;
  status: string;
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
  created_at: string;
  asset_count: number;
  draft_count: number;
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
