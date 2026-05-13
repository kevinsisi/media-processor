// HTTP client for the media-processor API.
//
// Base URL resolution:
//   1. VITE_API_URL env var if set (use for prod or non-proxied dev)
//   2. "/api" default — vite dev server proxies /api → http://localhost:8623
//      (see web/vite.config.ts), and prod deployments are expected to expose
//      the backend under the same /api path via reverse proxy.

import type {
  AnalyzeRequest,
  AnalyzeResponse,
  AssetBatchDeleteOut,
  AssetDeleteOut,
  AssetDetail,
  AssetStabilizeRequest,
  AssetStabilizeResponse,
  AssetThumbnailsOut,
  AssetVariantPatch,
  AssetVariantResponse,
  BgmGenerationStatus,
  CropRegionPatch,
  DetectedClassOut,
  DraftComment,
  DraftCommentCreate,
  DraftDetail,
  DraftExportArtifact,
  DraftExportRequest,
  DraftExportResponse,
  DraftRebuildSubtitlesRequest,
  DraftReorderRequest,
  DraftSegmentPatch,
  DraftSegmentSplitRequest,
  DraftSummary,
  EditTriggerRequest,
  EditTriggerResponse,
  LLMKeysUpdateIn,
  LLMKeysUpdateOut,
  MusicLibraryResponse,
  MusicSuggestion,
  ProjectAnalysisOut,
  ProjectCreate,
  ProjectDetail,
  ProjectSummary,
  QueueStatusOut,
  ReviewCreate,
  ReviewOut,
  ScriptCoverageOut,
  ScriptOut,
  ScriptUpsert,
  SegmentVolumeOut,
  SegmentVolumePatch,
  SettingsOut,
  SmartCameraPatch,
  SubjectClassPatch,
  SubtitleCueOut,
  SubtitleCuePatch,
  SubtitleStylePatch,
  SyncFromManagerIn,
  SyncFromManagerOut,
  TrackingDetailOut,
  TrackingTargetRequest,
  TrackingTargetResponse,
  TranscriptOut,
  TranscriptUpsert,
  TranslateSubtitleRequest,
  TranslateSubtitleResponse,
  UploadCompleteOut,
  UploadSessionCreate,
  UploadSessionOut,
  WatermarkPresetApplyRequest,
  WatermarkPresetOut,
  WatermarkPresetSaveRequest,
  WatermarkSettingsPatch,
} from "./types";

const DEFAULT_BASE_URL = import.meta.env.VITE_API_URL ?? "/api";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly url: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiClientOptions {
  baseUrl?: string;
  fetch?: typeof fetch;
}

export class ApiClient {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = options.baseUrl ?? DEFAULT_BASE_URL;
    this.fetchImpl = options.fetch ?? fetch.bind(globalThis);
  }

  fetchProjects(): Promise<ProjectSummary[]> {
    return this.get<ProjectSummary[]>("/projects");
  }

  fetchProject(id: number): Promise<ProjectDetail> {
    return this.get<ProjectDetail>(`/projects/${id}`);
  }

  fetchProjectDrafts(id: number): Promise<DraftSummary[]> {
    return this.get<DraftSummary[]>(`/projects/${id}/drafts`);
  }

  fetchDraft(id: number): Promise<DraftDetail> {
    return this.get<DraftDetail>(`/drafts/${id}`);
  }

  fetchAsset(id: number): Promise<AssetDetail> {
    return this.get<AssetDetail>(`/assets/${id}`);
  }

  fetchAssetThumbnails(assetId: number): Promise<AssetThumbnailsOut> {
    return this.get<AssetThumbnailsOut>(`/assets/${assetId}/thumbnails`);
  }

  stabilizeAsset(
    assetId: number,
    payload: AssetStabilizeRequest = {},
  ): Promise<AssetStabilizeResponse> {
    return this.request<AssetStabilizeResponse>(`/assets/${assetId}/stabilize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  patchAssetVariant(
    assetId: number,
    payload: AssetVariantPatch,
  ): Promise<AssetVariantResponse> {
    return this.request<AssetVariantResponse>(`/assets/${assetId}/variant`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  postReview(payload: ReviewCreate): Promise<ReviewOut> {
    return this.request<ReviewOut>("/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  createProject(payload: ProjectCreate): Promise<ProjectDetail> {
    return this.request<ProjectDetail>("/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  // v0.18 — patch the project-level subtitle burn-in style. Send a
  // partial diff; unspecified fields keep their existing value.
  patchProjectSubtitleStyle(
    projectId: number,
    payload: SubtitleStylePatch,
  ): Promise<ProjectDetail> {
    return this.request<ProjectDetail>(
      `/projects/${projectId}/subtitle-style`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  // v0.21 — set / clear the auto-edit subject-class filter. ``null``
  // restores the historical "every asset eligible at full duration"
  // behaviour; a non-null value must be one of the 80 COCO class
  // names (server-side validation rejects others with HTTP 422).
  patchProjectSubjectClass(
    projectId: number,
    payload: SubjectClassPatch,
  ): Promise<ProjectDetail> {
    return this.request<ProjectDetail>(
      `/projects/${projectId}/subject-class`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  // v0.29.0 — set / clear the static-crop anchor used when source
  // orientation differs from target orientation. Body
  // ``{x_norm: null, y_norm: null}`` clears the override (revert to
  // centre); both populated stores a custom anchor 0..1.
  patchProjectCropRegion(
    projectId: number,
    payload: CropRegionPatch,
  ): Promise<ProjectDetail> {
    return this.request<ProjectDetail>(
      `/projects/${projectId}/crop-region`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  // v0.30.0 — flip the persistent AI Smart Camera toggle. Default
  // ``false`` (opt-in feature). Per-run overrides ride on
  // EditTriggerRequest.smart_camera; this PATCH is the cheap durable
  // setting the FE writes when the operator ticks the experimental
  // checkbox.
  patchProjectSmartCamera(
    projectId: number,
    payload: SmartCameraPatch,
  ): Promise<ProjectDetail> {
    return this.request<ProjectDetail>(
      `/projects/${projectId}/smart-camera`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  // v0.24.0 — set the BGM tail-fade duration. Server clamps to
  // [0.0, 10.0]. The mixer reads ``Project.bgm_fade_out_sec`` on
  // every render — no separate trigger; the next re-render picks it up.
  patchProjectBgmFadeOut(
    projectId: number,
    fadeOutSec: number,
  ): Promise<ProjectDetail> {
    return this.request<ProjectDetail>(
      `/projects/${projectId}/bgm-fade-out`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fade_out_sec: fadeOutSec }),
      },
    );
  }

  // v0.26.0 / v0.27.1 — single-asset deletion. Pre-0.27.1 returned
  // 204 on success and 409 when an active draft still referenced
  // the asset. v0.27.1 always returns a JSON body: ``deleted=false``
  // with a non-empty ``affected_drafts`` list signals the caller to
  // confirm with the user and retry with ``force=true``; ``deleted
  // =true`` plus ``invalidated_versions`` echoes which draft
  // versions were flipped to ``failed`` so the FE can show
  // "v3 已被標為失敗".
  deleteAsset(
    assetId: number,
    options: { force?: boolean } = {},
  ): Promise<AssetDeleteOut> {
    const qs = options.force ? "?force=true" : "";
    return this.request<AssetDeleteOut>(`/assets/${assetId}${qs}`, {
      method: "DELETE",
    });
  }

  // v0.26.0 / v0.27.1 — batch asset delete with per-row outcomes.
  // v0.27.1 threads ``force`` through to the endpoint; without it,
  // rows whose deletion would invalidate an active draft come back
  // with ``deleted=false`` + ``affected_drafts`` populated. The FE
  // confirms with the user and re-issues the SAME body with
  // ``force=true`` to actually delete.
  batchDeleteAssets(
    projectId: number,
    assetIds: number[],
    options: { force?: boolean } = {},
  ): Promise<AssetBatchDeleteOut> {
    const qs = options.force ? "?force=true" : "";
    return this.request<AssetBatchDeleteOut>(
      `/projects/${projectId}/assets/batch${qs}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset_ids: assetIds }),
      },
    );
  }

  // v0.25.0 / v0.27.0 — RQ queue inspector. Returns all running jobs
  // plus the ordered queued list across analysis / editing / bgm queues.
  // The FE polls every few seconds while work is in flight.
  getQueueStatus(): Promise<QueueStatusOut> {
    return this.request<QueueStatusOut>("/queue/status", { method: "GET" });
  }

  // v0.25.0 — drop a queued job. 409s when the job is already
  // running (use POST /drafts/{id}/cancel for live render kills).
  // Uses the raw fetch path because the response is 204 No Content
  // and the json-deserialising request helper would throw on the
  // empty body.
  async cancelQueuedJob(jobId: string): Promise<void> {
    const url = `${this.baseUrl}/queue/jobs/${encodeURIComponent(jobId)}`;
    const response = await this.fetchImpl(url, { method: "DELETE" });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new ApiError(response.status, url, text || response.statusText);
    }
  }

  // v0.21 — class summary across this project's tracking_json blobs.
  // Empty list when no asset has been tracked yet — UI surfaces a
  // hint to run analysis first instead of offering a fake menu.
  fetchProjectDetectedClasses(
    projectId: number,
  ): Promise<DetectedClassOut[]> {
    return this.get<DetectedClassOut[]>(
      `/projects/${projectId}/detected-classes`,
    );
  }

  // v0.14.5 — single-shot multipart BGM upload. Backend caps at 50 MB
  // and rewrites prior BGM at the same project_id, so a re-upload is
  // safe. We let the browser set Content-Type for FormData (boundary).
  uploadProjectBgm(projectId: number, file: File): Promise<ProjectDetail> {
    const fd = new FormData();
    fd.append("file", file);
    return this.request<ProjectDetail>(`/projects/${projectId}/bgm`, {
      method: "POST",
      body: fd,
    });
  }

  async deleteProjectBgm(projectId: number): Promise<void> {
    const url = `${this.baseUrl}/projects/${projectId}/bgm`;
    const response = await this.fetchImpl(url, { method: "DELETE" });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new ApiError(response.status, url, text || response.statusText);
    }
  }

  // v0.18 — brand watermark / logo overlay. PNG only, ≤5 MB. Returns
  // the refreshed ProjectDetail so the picker can re-render against
  // the new watermark_url (carries a cache-bust query).
  uploadProjectWatermark(
    projectId: number,
    file: File,
  ): Promise<ProjectDetail> {
    const fd = new FormData();
    fd.append("file", file);
    return this.request<ProjectDetail>(`/projects/${projectId}/watermark`, {
      method: "POST",
      body: fd,
    });
  }

  updateProjectWatermark(
    projectId: number,
    payload: WatermarkSettingsPatch,
  ): Promise<ProjectDetail> {
    return this.request<ProjectDetail>(`/projects/${projectId}/watermark`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async deleteProjectWatermark(projectId: number): Promise<void> {
    const url = `${this.baseUrl}/projects/${projectId}/watermark`;
    const response = await this.fetchImpl(url, { method: "DELETE" });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new ApiError(response.status, url, text || response.statusText);
    }
  }

  // ----- v0.21.6 — watermark presets -----

  fetchWatermarkPresets(): Promise<WatermarkPresetOut[]> {
    return this.get<WatermarkPresetOut[]>("/watermark-presets");
  }

  saveWatermarkPreset(
    payload: WatermarkPresetSaveRequest,
  ): Promise<WatermarkPresetOut> {
    return this.request<WatermarkPresetOut>("/watermark-presets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async deleteWatermarkPreset(presetId: number): Promise<void> {
    const url = `${this.baseUrl}/watermark-presets/${presetId}`;
    const response = await this.fetchImpl(url, { method: "DELETE" });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new ApiError(response.status, url, text || response.statusText);
    }
  }

  applyWatermarkPreset(
    projectId: number,
    payload: WatermarkPresetApplyRequest,
  ): Promise<ProjectDetail> {
    return this.request<ProjectDetail>(
      `/projects/${projectId}/watermark/apply-preset`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  // ----- v0.15 — AI BGM gen + music library -----

  fetchMusicSuggestion(
    projectId: number,
    stylePreset?: string,
  ): Promise<MusicSuggestion> {
    const qs =
      stylePreset && stylePreset !== "custom"
        ? `?style_preset=${encodeURIComponent(stylePreset)}`
        : "";
    return this.get<MusicSuggestion>(
      `/projects/${projectId}/music-suggestion${qs}`,
    );
  }

  generateProjectBgm(
    projectId: number,
    prompt: string,
  ): Promise<BgmGenerationStatus> {
    return this.request<BgmGenerationStatus>(
      `/projects/${projectId}/generate-bgm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      },
    );
  }

  fetchProjectBgmStatus(projectId: number): Promise<BgmGenerationStatus> {
    return this.get<BgmGenerationStatus>(`/projects/${projectId}/bgm-status`);
  }

  fetchMusicLibrary(): Promise<MusicLibraryResponse> {
    return this.get<MusicLibraryResponse>("/music-library");
  }

  selectLibraryBgm(
    projectId: number,
    name: string,
  ): Promise<BgmGenerationStatus> {
    return this.request<BgmGenerationStatus>(
      `/projects/${projectId}/bgm/select-library`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      },
    );
  }

  async fetchScript(projectId: number): Promise<ScriptOut | null> {
    try {
      return await this.get<ScriptOut>(`/projects/${projectId}/script`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }

  putScript(projectId: number, payload: ScriptUpsert): Promise<ScriptOut> {
    return this.request<ScriptOut>(`/projects/${projectId}/script`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  createUploadSession(
    projectId: number,
    payload: UploadSessionCreate,
  ): Promise<UploadSessionOut> {
    return this.request<UploadSessionOut>(`/projects/${projectId}/uploads`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  fetchUploadSession(sessionId: string): Promise<UploadSessionOut> {
    return this.get<UploadSessionOut>(`/uploads/${sessionId}`);
  }

  completeUploadSession(sessionId: string): Promise<UploadCompleteOut> {
    return this.request<UploadCompleteOut>(`/uploads/${sessionId}/complete`, {
      method: "POST",
    });
  }

  uploadChunkUrl(sessionId: string, index: number): string {
    return `${this.baseUrl}/uploads/${sessionId}/chunks/${index}`;
  }

  // ----- M4 — analysis endpoints -----

  fetchProjectAnalysis(projectId: number): Promise<ProjectAnalysisOut> {
    return this.get<ProjectAnalysisOut>(`/projects/${projectId}/assets`);
  }

  async fetchTranscript(assetId: number): Promise<TranscriptOut | null> {
    try {
      return await this.get<TranscriptOut>(`/assets/${assetId}/transcript`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }

  putTranscript(
    assetId: number,
    payload: TranscriptUpsert,
  ): Promise<TranscriptOut> {
    return this.request<TranscriptOut>(`/assets/${assetId}/transcript`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async fetchCoverage(assetId: number): Promise<ScriptCoverageOut | null> {
    try {
      return await this.get<ScriptCoverageOut>(`/assets/${assetId}/coverage`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }

  triggerAnalyze(
    assetId: number,
    payload: AnalyzeRequest = {},
  ): Promise<AnalyzeResponse> {
    return this.request<AnalyzeResponse>(`/assets/${assetId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  // ----- v0.18 — secondary-language subtitle (Whisper translate) -----

  triggerSubtitleTranslate(
    assetId: number,
    payload: TranslateSubtitleRequest = {},
  ): Promise<TranslateSubtitleResponse> {
    return this.request<TranslateSubtitleResponse>(
      `/assets/${assetId}/translate-subtitle`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lang: payload.lang ?? "en" }),
      },
    );
  }

  // ----- v0.17 — tracking-target picker -----

  async fetchAssetTracking(
    assetId: number,
  ): Promise<TrackingDetailOut | null> {
    try {
      return await this.get<TrackingDetailOut>(`/assets/${assetId}/tracking`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }

  patchAssetTrackingTarget(
    assetId: number,
    payload: TrackingTargetRequest,
  ): Promise<TrackingTargetResponse> {
    return this.request<TrackingTargetResponse>(
      `/assets/${assetId}/tracking-target`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  // ----- v0.17 — per-segment voice / BGM volume -----

  patchDraftSegmentVolume(
    draftId: number,
    segmentId: number,
    payload: SegmentVolumePatch,
  ): Promise<SegmentVolumeOut> {
    return this.request<SegmentVolumeOut>(
      `/drafts/${draftId}/segments/${segmentId}/volume`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  // ----- M5.2 — per-version comment thread -----

  fetchDraftComments(draftId: number): Promise<DraftComment[]> {
    return this.get<DraftComment[]>(`/drafts/${draftId}/comments`);
  }

  postDraftComment(
    draftId: number,
    payload: DraftCommentCreate,
  ): Promise<DraftComment> {
    return this.request<DraftComment>(`/drafts/${draftId}/comments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  // ----- M5 — auto-edit trigger -----

  triggerProjectEdit(
    projectId: number,
    payload: EditTriggerRequest = {},
  ): Promise<EditTriggerResponse> {
    return this.request<EditTriggerResponse>(`/projects/${projectId}/edit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  cancelDraftRender(draftId: number): Promise<DraftDetail> {
    return this.request<DraftDetail>(`/drafts/${draftId}/cancel`, {
      method: "POST",
    });
  }

  // ----- M7 — manual control -----

  reorderDraftSegments(
    draftId: number,
    payload: DraftReorderRequest,
  ): Promise<DraftDetail> {
    return this.request<DraftDetail>(`/drafts/${draftId}/order`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  // ----- v0.20 — timeline editor segment-level mutations -----
  //
  // None of these auto-enqueue a render. Use ``reorderDraftSegments``
  // with the current order list to fire the existing skip-plan render
  // path once the operator clicks the timeline editor's "Apply"
  // button.

  splitDraftSegment(
    draftId: number,
    segId: number,
    payload: DraftSegmentSplitRequest,
  ): Promise<DraftDetail> {
    return this.request<DraftDetail>(
      `/drafts/${draftId}/segments/${segId}/split`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  patchDraftSegment(
    draftId: number,
    segId: number,
    payload: DraftSegmentPatch,
  ): Promise<DraftDetail> {
    return this.request<DraftDetail>(
      `/drafts/${draftId}/segments/${segId}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  async deleteDraftSegment(draftId: number, segId: number): Promise<void> {
    await this.request<void>(`/drafts/${draftId}/segments/${segId}`, {
      method: "DELETE",
    });
  }

  // Resolve a public URL for an asset's source MP4 so a <video> element
  // can scrub it directly. ``asset.file_path`` is the absolute
  // in-container path like ``/app/media/assets/12/IMG_1234.MOV``; we
  // pull the last two path components ({project_id}/{filename}) and
  // bolt them onto ``/media/assets`` (which is StaticFiles-mounted by
  // api/main.py and reverse-proxied under ``/api`` in prod).
  assetVideoUrl(asset: {
    file_path: string;
    active_asset_variant?: string;
    variant_urls?: Record<string, string | null>;
  }): string {
    const active = asset.active_asset_variant ?? "raw";
    const variantUrl = asset.variant_urls?.[active];
    if (variantUrl) {
      return variantUrl.startsWith("/api/") ? variantUrl : `${this.baseUrl}${variantUrl}`;
    }
    const parts = asset.file_path.replace(/\\/g, "/").split("/").filter(Boolean);
    const tail = parts.slice(-2).join("/");
    return `${this.baseUrl}/media/assets/${tail}`;
  }

  fetchDraftSubtitles(draftId: number): Promise<SubtitleCueOut[]> {
    return this.get<SubtitleCueOut[]>(`/drafts/${draftId}/subtitles`);
  }

  patchDraftSubtitle(
    draftId: number,
    idx: number,
    payload: SubtitleCuePatch,
  ): Promise<SubtitleCueOut> {
    return this.request<SubtitleCueOut>(
      `/drafts/${draftId}/subtitles/${idx}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  rebuildDraftSubtitles(
    draftId: number,
    payload?: DraftRebuildSubtitlesRequest,
  ): Promise<DraftDetail> {
    const init: RequestInit = { method: "POST" };
    if (payload) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(payload);
    }
    return this.request<DraftDetail>(
      `/drafts/${draftId}/rebuild-subtitles`,
      init,
    );
  }

  // v0.22.1 — re-render an existing draft against the current
  // project settings without letting the AI re-shuffle segments.
  // Body shape is shared with rebuildDraftSubtitles since both
  // endpoints take the same render-flag override.
  reRenderDraft(
    draftId: number,
    payload?: DraftRebuildSubtitlesRequest,
  ): Promise<DraftDetail> {
    const init: RequestInit = { method: "POST" };
    if (payload) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(payload);
    }
    return this.request<DraftDetail>(`/drafts/${draftId}/re-render`, init);
  }

  exportDraft(
    draftId: number,
    payload: DraftExportRequest,
  ): Promise<DraftExportResponse> {
    return this.request<DraftExportResponse>(`/drafts/${draftId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  fetchDraftExports(draftId: number): Promise<DraftExportArtifact[]> {
    return this.get<DraftExportArtifact[]>(`/drafts/${draftId}/exports`);
  }

  // ----- Settings — LLM key pool -----

  fetchSettings(): Promise<SettingsOut> {
    return this.get<SettingsOut>("/settings");
  }

  updateLLMKeys(payload: LLMKeysUpdateIn): Promise<LLMKeysUpdateOut> {
    return this.request<LLMKeysUpdateOut>("/settings/llm-api-keys", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async clearLLMKeys(): Promise<void> {
    const url = `${this.baseUrl}/settings/llm-api-keys`;
    const response = await this.fetchImpl(url, { method: "DELETE" });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new ApiError(response.status, url, text || response.statusText);
    }
  }

  syncKeysFromManager(
    payload: SyncFromManagerIn = {},
  ): Promise<SyncFromManagerOut> {
    return this.request<SyncFromManagerOut>(
      "/settings/sync-from-key-manager",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      },
    );
  }

  private get<T>(path: string): Promise<T> {
    return this.request<T>(path, { method: "GET" });
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const response = await this.fetchImpl(url, init);
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new ApiError(response.status, url, text || response.statusText);
    }
    return (await response.json()) as T;
  }
}

export const apiClient = new ApiClient();
