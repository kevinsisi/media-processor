// HTTP client for the media-processor API.
//
// The mockup screens (ProjectList, Review) intentionally still consume mock
// data — wiring them to the live backend is M3/M4 work. Shipping the typed
// surface in M2 keeps that wiring mechanical.

import type {
  AssetDetail,
  DraftDetail,
  DraftSummary,
  ProjectDetail,
  ProjectSummary,
  ReviewCreate,
  ReviewOut,
} from "./types";

const DEFAULT_BASE_URL = "/api";

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

  postReview(payload: ReviewCreate): Promise<ReviewOut> {
    return this.request<ReviewOut>("/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
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
