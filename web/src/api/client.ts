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
  AssetDetail,
  AssetThumbnailsOut,
  BgmGenerationStatus,
  DetectedClassOut,
  DraftComment,
  DraftCommentCreate,
  DraftDetail,
  DraftExportRequest,
  DraftExportResponse,
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
  ReviewCreate,
  ReviewOut,
  ScriptCoverageOut,
  ScriptOut,
  ScriptUpsert,
  SegmentVolumeOut,
  SegmentVolumePatch,
  SettingsOut,
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
  assetVideoUrl(asset: { file_path: string }): string {
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

  rebuildDraftSubtitles(draftId: number): Promise<DraftDetail> {
    return this.request<DraftDetail>(`/drafts/${draftId}/rebuild-subtitles`, {
      method: "POST",
    });
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
