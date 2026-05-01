// Browser chunked-upload driver.
//
// One ``runChunkedUpload`` call walks a single file from start (or resume)
// through PUT-per-chunk to ``complete``, emitting per-chunk progress so the
// caller can drive a progress bar. Uses XMLHttpRequest (not fetch) because
// fetch has no request-progress event.

import type {
  UploadCompleteOut,
  UploadKind,
  UploadSessionOut,
} from "../api/types";
import { apiClient } from "../api/client";

export const DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024; // 4 MiB
const PER_CHUNK_RETRIES = 3;

const STORAGE_KEY = "mp.upload.sessions.v1";

export interface UploadProgress {
  uploadedBytes: number;
  totalBytes: number;
  chunkIndex: number;
  totalChunks: number;
}

export interface RunChunkedUploadOptions {
  projectId: number;
  file: File;
  kind: UploadKind;
  chunkSize?: number;
  onSession?: (session: UploadSessionOut) => void;
  onProgress?: (progress: UploadProgress) => void;
  signal?: AbortSignal;
}

interface SessionMap {
  [fingerprint: string]: string;
}

function loadSessionMap(): SessionMap {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as SessionMap) : {};
  } catch {
    return {};
  }
}

function saveSessionMap(map: SessionMap): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch {
    // Quota or disabled storage — proceed without resume across reload.
  }
}

export function fingerprintFile(projectId: number, file: File): string {
  return `p${projectId}:${file.name}:${file.size}:${file.lastModified}`;
}

export function rememberSession(fingerprint: string, sessionId: string): void {
  const map = loadSessionMap();
  map[fingerprint] = sessionId;
  saveSessionMap(map);
}

export function forgetSession(fingerprint: string): void {
  const map = loadSessionMap();
  if (fingerprint in map) {
    delete map[fingerprint];
    saveSessionMap(map);
  }
}

export function recallSession(fingerprint: string): string | null {
  return loadSessionMap()[fingerprint] ?? null;
}

function expectedChunkCount(totalBytes: number, chunkSize: number): number {
  if (totalBytes === 0) return 0;
  return Math.ceil(totalBytes / chunkSize);
}

async function putChunk(
  url: string,
  blob: Blob,
  signal: AbortSignal | undefined,
  attempt: number,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    const onAbort = () => xhr.abort();
    if (signal) {
      if (signal.aborted) {
        reject(new DOMException("aborted", "AbortError"));
        return;
      }
      signal.addEventListener("abort", onAbort, { once: true });
    }
    xhr.onload = () => {
      signal?.removeEventListener("abort", onAbort);
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(
          new Error(
            `chunk PUT failed (attempt ${attempt}): ${xhr.status} ${xhr.statusText}`,
          ),
        );
      }
    };
    xhr.onerror = () => {
      signal?.removeEventListener("abort", onAbort);
      reject(new Error(`chunk PUT network error (attempt ${attempt})`));
    };
    xhr.onabort = () => {
      signal?.removeEventListener("abort", onAbort);
      reject(new DOMException("aborted", "AbortError"));
    };
    xhr.send(blob);
  });
}

async function putChunkWithRetry(
  url: string,
  blob: Blob,
  signal: AbortSignal | undefined,
): Promise<void> {
  let lastErr: unknown = null;
  for (let attempt = 1; attempt <= PER_CHUNK_RETRIES; attempt += 1) {
    try {
      await putChunk(url, blob, signal, attempt);
      return;
    } catch (err) {
      if ((err as { name?: string }).name === "AbortError") throw err;
      lastErr = err;
      // Exponential backoff: 250ms, 750ms.
      const wait = 250 * Math.pow(3, attempt - 1);
      await new Promise<void>((r) => setTimeout(r, wait));
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error("chunk PUT failed");
}

export async function runChunkedUpload(
  options: RunChunkedUploadOptions,
): Promise<UploadCompleteOut> {
  const chunkSize = options.chunkSize ?? DEFAULT_CHUNK_SIZE;
  const fingerprint = fingerprintFile(options.projectId, options.file);
  let session: UploadSessionOut | null = null;

  const remembered = recallSession(fingerprint);
  if (remembered) {
    try {
      session = await apiClient.fetchUploadSession(remembered);
      if (
        session.filename !== options.file.name ||
        session.total_size !== options.file.size ||
        session.chunk_size !== chunkSize
      ) {
        // Mismatched session — start fresh.
        forgetSession(fingerprint);
        session = null;
      }
    } catch {
      forgetSession(fingerprint);
      session = null;
    }
  }

  if (session === null) {
    session = await apiClient.createUploadSession(options.projectId, {
      kind: options.kind,
      filename: options.file.name,
      total_size: options.file.size,
      chunk_size: chunkSize,
    });
    rememberSession(fingerprint, session.id);
  }

  options.onSession?.(session);

  const totalChunks = expectedChunkCount(options.file.size, chunkSize);
  const received = new Set<number>(session.received_chunks);

  for (let i = 0; i < totalChunks; i += 1) {
    if (options.signal?.aborted) {
      throw new DOMException("aborted", "AbortError");
    }
    if (received.has(i)) {
      options.onProgress?.({
        uploadedBytes: Math.min((i + 1) * chunkSize, options.file.size),
        totalBytes: options.file.size,
        chunkIndex: i,
        totalChunks,
      });
      continue;
    }
    const start = i * chunkSize;
    const end = Math.min(start + chunkSize, options.file.size);
    const blob = options.file.slice(start, end);
    await putChunkWithRetry(
      apiClient.uploadChunkUrl(session.id, i),
      blob,
      options.signal,
    );
    received.add(i);
    options.onProgress?.({
      uploadedBytes: end,
      totalBytes: options.file.size,
      chunkIndex: i,
      totalChunks,
    });
  }

  const result = await apiClient.completeUploadSession(session.id);
  forgetSession(fingerprint);
  return result;
}
