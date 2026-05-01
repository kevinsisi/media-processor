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
  DraftDetail,
  DraftSummary,
  EditTriggerRequest,
  EditTriggerResponse,
  LLMKeysUpdateIn,
  LLMKeysUpdateOut,
  ProjectAnalysisOut,
  ProjectCreate,
  ProjectDetail,
  ProjectSummary,
  ReviewCreate,
  ReviewOut,
  ScriptCoverageOut,
  ScriptOut,
  ScriptUpsert,
  SettingsOut,
  SyncFromManagerIn,
  SyncFromManagerOut,
  TranscriptOut,
  TranscriptUpsert,
  UploadCompleteOut,
  UploadSessionCreate,
  UploadSessionOut,
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
