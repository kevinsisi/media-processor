import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiClient } from "../api/client";
import type { ProjectDetail } from "../api/types";
import {
  fingerprintFile,
  forgetSession,
  rememberSession,
  runChunkedUpload,
} from "../upload/chunked";
import "./Upload.css";

type RowState = "queued" | "uploading" | "complete" | "error";

interface VideoRow {
  id: string;
  file: File;
  state: RowState;
  uploadedBytes: number;
  totalBytes: number;
  errorMessage: string | null;
  abort: AbortController;
}

const SCRIPT_DEBOUNCE_MS = 600;
// Cap on simultaneous in-flight video uploads. Without this, picking N
// files fans out N runChunkedUpload() calls in parallel, each PUTting
// 4 MiB chunks. The api's single-event-loop write path then queues
// every chunk write end-to-end and slow-disk situations exceed the
// nginx proxy timeout → 502s back to the browser. Three at a time
// keeps the per-file UI responsive and stays well under the api's
// thread-pool default.
const MAX_CONCURRENT_UPLOADS = 3;

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatPct(uploaded: number, total: number): string {
  if (total === 0) return "0%";
  return `${Math.min(100, Math.round((uploaded / total) * 100))}%`;
}

function formatSavedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mi}`;
}

export default function Upload() {
  const params = useParams<{ id: string }>();
  const projectId = params.id ? Number(params.id) : NaN;

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [projectError, setProjectError] = useState<string | null>(null);

  const [videoRows, setVideoRows] = useState<VideoRow[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [completedAssetIds, setCompletedAssetIds] = useState<number[]>([]);
  // Track row IDs currently mid-upload independently of React state so
  // the drain function can decide synchronously whether a slot is free
  // without waiting for setVideoRows to flush.
  const inFlightIdsRef = useRef<Set<string>>(new Set());
  const videoRowsRef = useRef<VideoRow[]>([]);
  videoRowsRef.current = videoRows;

  // Script state
  const [scriptBody, setScriptBody] = useState("");
  const [scriptSourceFilename, setScriptSourceFilename] = useState<string | null>(null);
  const [scriptInitialLoaded, setScriptInitialLoaded] = useState(false);
  const [scriptSavedAt, setScriptSavedAt] = useState<string | null>(null);
  const [scriptDirty, setScriptDirty] = useState(false);
  const [scriptError, setScriptError] = useState<string | null>(null);
  const scriptDebounceRef = useRef<number | null>(null);

  // Load project + script on mount
  useEffect(() => {
    if (Number.isNaN(projectId)) return;
    let cancelled = false;
    (async () => {
      try {
        const p = await apiClient.fetchProject(projectId);
        if (!cancelled) setProject(p);
      } catch (err) {
        if (!cancelled) setProjectError(err instanceof Error ? err.message : "讀取失敗");
      }
      try {
        const s = await apiClient.fetchScript(projectId);
        if (!cancelled && s !== null) {
          setScriptBody(s.body);
          setScriptSourceFilename(s.source_filename);
          setScriptSavedAt(s.updated_at);
        }
      } catch {
        // 404 already handled inside fetchScript; other errors surface lazily.
      } finally {
        if (!cancelled) setScriptInitialLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Debounced script save
  useEffect(() => {
    if (!scriptInitialLoaded) return;
    if (!scriptDirty) return;
    if (scriptDebounceRef.current !== null) {
      window.clearTimeout(scriptDebounceRef.current);
    }
    scriptDebounceRef.current = window.setTimeout(async () => {
      try {
        const saved = await apiClient.putScript(projectId, {
          body: scriptBody,
          source_filename: scriptSourceFilename,
        });
        setScriptSavedAt(saved.updated_at);
        setScriptDirty(false);
        setScriptError(null);
      } catch (err) {
        setScriptError(err instanceof Error ? err.message : "儲存失敗");
      }
    }, SCRIPT_DEBOUNCE_MS);
    return () => {
      if (scriptDebounceRef.current !== null) {
        window.clearTimeout(scriptDebounceRef.current);
      }
    };
  }, [
    scriptBody,
    scriptSourceFilename,
    scriptDirty,
    scriptInitialLoaded,
    projectId,
  ]);

  function pickVideoFiles(files: FileList | null): void {
    if (!files || files.length === 0) return;
    const fresh: VideoRow[] = [];
    for (const file of Array.from(files)) {
      fresh.push({
        id: crypto.randomUUID(),
        file,
        state: "queued",
        uploadedBytes: 0,
        totalBytes: file.size,
        errorMessage: null,
        abort: new AbortController(),
      });
    }
    setVideoRows((prev) => [...prev, ...fresh]);
    // Defer the drain to the next microtask so the setVideoRows state
    // update lands first; videoRowsRef catches up via the inline assign.
    Promise.resolve().then(drainUploadQueue);
  }

  function drainUploadQueue(): void {
    // Start additional uploads while the in-flight count is under cap
    // AND there are queued rows. Reads from videoRowsRef so we see the
    // latest committed state rather than stale closure values.
    const inFlight = inFlightIdsRef.current;
    for (const row of videoRowsRef.current) {
      if (inFlight.size >= MAX_CONCURRENT_UPLOADS) break;
      if (row.state !== "queued") continue;
      if (inFlight.has(row.id)) continue;
      inFlight.add(row.id);
      void beginUpload(row);
    }
  }

  async function beginUpload(initial: VideoRow): Promise<void> {
    // ``drainUploadQueue`` is the only caller and reserves the slot in
    // ``inFlightIdsRef`` before invoking us; ``finally`` below releases
    // it on every exit path.
    setVideoRows((prev) =>
      prev.map((r) =>
        r.id === initial.id ? { ...r, state: "uploading", errorMessage: null } : r,
      ),
    );
    try {
      const result = await runChunkedUpload({
        projectId,
        file: initial.file,
        kind: "video",
        signal: initial.abort.signal,
        onSession: (session) => {
          rememberSession(fingerprintFile(projectId, initial.file), session.id);
        },
        onProgress: (p) => {
          setVideoRows((prev) =>
            prev.map((r) =>
              r.id === initial.id
                ? { ...r, uploadedBytes: p.uploadedBytes }
                : r,
            ),
          );
        },
      });
      forgetSession(fingerprintFile(projectId, initial.file));
      setVideoRows((prev) =>
        prev.map((r) =>
          r.id === initial.id
            ? {
                ...r,
                state: "complete",
                uploadedBytes: r.totalBytes,
              }
            : r,
        ),
      );
      if (result.asset !== null) {
        setCompletedAssetIds((prev) => [...prev, result.asset!.id]);
      }
      // Refresh project counts
      try {
        const p = await apiClient.fetchProject(projectId);
        setProject(p);
      } catch {
        // tolerate
      }
    } catch (err) {
      if ((err as { name?: string }).name === "AbortError") {
        return;
      }
      setVideoRows((prev) =>
        prev.map((r) =>
          r.id === initial.id
            ? {
                ...r,
                state: "error",
                errorMessage: err instanceof Error ? err.message : "上傳失敗",
              }
            : r,
        ),
      );
    } finally {
      // Free this row's slot on every exit path (success / error /
      // aborted) and start the next queued upload if there is one.
      inFlightIdsRef.current.delete(initial.id);
      drainUploadQueue();
    }
  }

  function retryUpload(rowId: string): void {
    const row = videoRowsRef.current.find((r) => r.id === rowId);
    if (!row) return;
    const fresh: VideoRow = {
      ...row,
      state: "queued",
      errorMessage: null,
      abort: new AbortController(),
    };
    setVideoRows((prev) => prev.map((r) => (r.id === rowId ? fresh : r)));
    Promise.resolve().then(drainUploadQueue);
  }

  function uploadScriptFile(file: File): void {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      setScriptBody(text);
      setScriptSourceFilename(file.name);
      setScriptDirty(true);
    };
    reader.onerror = () => {
      setScriptError("讀取檔案失敗");
    };
    reader.readAsText(file, "utf-8");
  }

  const charCount = useMemo(() => scriptBody.length, [scriptBody]);
  const completedVideoCount = videoRows.filter((r) => r.state === "complete").length;
  const pendingUploadCount = useMemo(
    () =>
      videoRows.filter((r) => r.state === "queued" || r.state === "uploading")
        .length,
    [videoRows],
  );

  // Browser-level guard: if any uploads are still in flight when the
  // user tries to navigate away (close tab, refresh, hit Back), surface
  // the confirm dialog so they don't lose progress. Modern browsers
  // ignore the custom message text and show their own generic prompt;
  // setting returnValue to a non-empty string is what actually triggers
  // the confirm. The handler is registered only while pending > 0 so we
  // never block clean navigation.
  useEffect(() => {
    if (pendingUploadCount === 0) return;
    const handler = (event: BeforeUnloadEvent) => {
      const message = `有 ${pendingUploadCount} 個影片還在上傳中，離開會放棄未完成的上傳。確定離開嗎？`;
      event.preventDefault();
      event.returnValue = message;
      return message;
    };
    window.addEventListener("beforeunload", handler);
    return () => {
      window.removeEventListener("beforeunload", handler);
    };
  }, [pendingUploadCount]);
  // Server count is authoritative once project re-fetches; otherwise count
  // what we know we've completed locally this session.
  const assetCount = project?.asset_count ?? completedAssetIds.length;
  const hasScript = scriptBody.trim().length > 0;

  if (Number.isNaN(projectId)) {
    return (
      <main className="page upload">
        <p className="upload-error">專案編號無效</p>
      </main>
    );
  }

  return (
    <main className="page upload">
      <section className="hero">
        <div className="hero__kicker">上傳素材</div>
        <h1 className="hero__title">
          專案 <em>#{String(projectId).padStart(3, "0")}</em>
        </h1>
        {project && (
          <p className="hero__lede">
            {project.client ? `${project.client} ／ ` : ""}
            {project.name} ・ 風格 <span className="mono">{project.profile_name}</span>
            ・ 比例 <span className="mono">{project.target_aspect_ratio}</span>
          </p>
        )}
        {projectError && (
          <p className="upload-error" role="alert">
            專案載入失敗 · {projectError}
          </p>
        )}
      </section>

      {/* Video section */}
      <section className="upload-section">
        <header className="upload-section__head">
          <h2 className="upload-section__title">影片</h2>
          <span className="upload-section__count">
            已完成 {completedVideoCount} / {videoRows.length}
          </span>
        </header>

        <div className="upload-drop">
          <p className="upload-drop__hint">
            點擊下方按鈕選取影片，可一次選多個檔案
          </p>
          <p className="upload-drop__hint upload-drop__hint--meta">
            上傳完成後會自動進行 AI 分析（語音轉錄、場景、運鏡、情緒、腳本對應）。可在「進入素材分析」查看每支影片的進度，分析完成後即可剪輯。
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            multiple
            className="upload-drop__input"
            onChange={(e) => {
              pickVideoFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <button
            type="button"
            className="upload-drop__btn"
            onClick={() => fileInputRef.current?.click()}
          >
            選取影片 +
          </button>
        </div>

        <ul className="upload-rows">
          {videoRows.map((row) => (
            <li
              key={row.id}
              className={`upload-row upload-row--${row.state}`}
            >
              <div className="upload-row__top">
                <span className="upload-row__name">{row.file.name}</span>
                <span className="upload-row__size mono">
                  {formatSize(row.uploadedBytes)} / {formatSize(row.totalBytes)}
                </span>
              </div>
              <div className="upload-row__bar">
                <div
                  className="upload-row__bar-fill"
                  style={{
                    width: formatPct(row.uploadedBytes, row.totalBytes),
                  }}
                />
              </div>
              <div className="upload-row__bottom">
                <span className="upload-row__state">
                  {row.state === "queued" && "佇列中"}
                  {row.state === "uploading" &&
                    `上傳中 ${formatPct(row.uploadedBytes, row.totalBytes)}`}
                  {row.state === "complete" && "已完成 · 自動分析中"}
                  {row.state === "error" && `失敗：${row.errorMessage ?? ""}`}
                </span>
                {row.state === "error" && (
                  <button
                    type="button"
                    className="upload-row__retry"
                    onClick={() => retryUpload(row.id)}
                  >
                    重試
                  </button>
                )}
              </div>
            </li>
          ))}
          {videoRows.length === 0 && (
            <li className="upload-rows__empty">尚未選擇影片</li>
          )}
        </ul>
      </section>

      {/* Script section */}
      <section className="upload-section">
        <header className="upload-section__head">
          <h2 className="upload-section__title">腳本</h2>
          <span className="upload-section__count">
            {scriptDirty
              ? "編輯中…"
              : scriptSavedAt
                ? `已儲存 · ${formatSavedAt(scriptSavedAt)}`
                : "尚未儲存"}
          </span>
        </header>

        <div className="script-toolbar">
          <label className="script-toolbar__upload">
            <input
              type="file"
              accept=".txt,text/plain"
              hidden
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) uploadScriptFile(file);
                e.target.value = "";
              }}
            />
            <span className="script-toolbar__upload-btn">上傳 .txt</span>
          </label>
          {scriptSourceFilename && (
            <span className="script-toolbar__source mono">
              來源：{scriptSourceFilename}
            </span>
          )}
          <span className="script-toolbar__count mono">{charCount} 字</span>
        </div>

        <textarea
          className="script-textarea"
          value={scriptBody}
          onChange={(e) => {
            setScriptBody(e.target.value);
            setScriptSourceFilename(null);
            setScriptDirty(true);
          }}
          placeholder="貼上或輸入這支影片的腳本…"
          rows={10}
        />

        {scriptError && (
          <p className="upload-error" role="alert">
            {scriptError}
          </p>
        )}
      </section>

      {/* Summary */}
      <section className="upload-summary">
        <div className="summary-grid">
          <div className="summary-cell">
            <span className="summary-cell__label">影片</span>
            <span className="summary-cell__value">{assetCount} 個</span>
          </div>
          <div className="summary-cell">
            <span className="summary-cell__label">腳本</span>
            <span className="summary-cell__value">{hasScript ? "已備妥" : "未備妥"}</span>
          </div>
          <div className="summary-cell">
            <span className="summary-cell__label">輸出比例</span>
            <span className="summary-cell__value mono">
              {project?.target_aspect_ratio ?? "-"}
            </span>
          </div>
        </div>
        <div className="summary-actions">
          <Link to="/" className="summary-back">
            ← 返回專案清單
          </Link>
          {/* v0.22 — disable the next-step CTA until at least one
              video has been uploaded, since the analysis page is
              empty without assets. ``pendingUploadCount`` keeps it
              dimmed while uploads are still flowing too, so users
              don't navigate away mid-upload by mistake. */}
          {assetCount === 0 ? (
            <span
              className="summary-next summary-next--disabled"
              aria-disabled="true"
              title="請先上傳至少一個影片，才能進入素材分析。"
            >
              進入素材分析 →
            </span>
          ) : pendingUploadCount > 0 ? (
            <Link
              to={`/projects/${projectId}/assets`}
              className="summary-next summary-next--warning"
              title={`還有 ${pendingUploadCount} 個影片未上傳完，前往分析頁可能看不到全部素材。`}
            >
              進入素材分析（{pendingUploadCount} 個還在上傳）→
            </Link>
          ) : (
            <Link to={`/projects/${projectId}/assets`} className="summary-next">
              進入素材分析 →
            </Link>
          )}
        </div>
      </section>
    </main>
  );
}
