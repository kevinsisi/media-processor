import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type {
  BgmGenerationStatus,
  MusicLibraryItem,
  ProjectDetail,
} from "../api/types";
import "./BgmSourcePicker.css";

type Source = "none" | "library" | "ai" | "upload";

interface BgmSourcePickerProps {
  projectId: number;
  bgmPath: string | null | undefined;
  onProjectUpdated: (project: ProjectDetail) => void;
  disabled?: boolean;
}

function bgmFilename(bgmPath: string | null | undefined): string | null {
  if (!bgmPath) return null;
  const sep = bgmPath.lastIndexOf("/");
  return sep >= 0 ? bgmPath.slice(sep + 1) : bgmPath;
}

// Backend status text → zh-Hant chip. Keep keys in sync with
// ``services.queue.GENERATE_BGM_FN`` + worker error tokens.
function labelForGenStatus(status: string | null): string {
  if (!status) return "尚未生成";
  if (status === "pending") return "排隊中";
  if (status === "running") return "生成中（約 30–60 秒）";
  if (status === "done") return "已完成";
  if (status.startsWith("failed:")) {
    const reason = status.slice("failed:".length);
    if (reason === "model-unavailable") return "失敗：模型未安裝";
    return `失敗：${reason}`;
  }
  return status;
}

export default function BgmSourcePicker({
  projectId,
  bgmPath,
  onProjectUpdated,
  disabled,
}: BgmSourcePickerProps) {
  const [source, setSource] = useState<Source>(() =>
    bgmPath ? "upload" : "none",
  );
  const filename = useMemo(() => bgmFilename(bgmPath), [bgmPath]);

  // ---- Library ----
  const [library, setLibrary] = useState<MusicLibraryItem[] | null>(null);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [libraryApplying, setLibraryApplying] = useState<string | null>(null);

  const loadLibrary = useCallback(async () => {
    setLibraryLoading(true);
    setLibraryError(null);
    try {
      const r = await apiClient.fetchMusicLibrary();
      setLibrary(r.items);
    } catch (err) {
      setLibraryError(err instanceof Error ? err.message : String(err));
    } finally {
      setLibraryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (source === "library" && library === null && !libraryLoading) {
      void loadLibrary();
    }
  }, [source, library, libraryLoading, loadLibrary]);

  const handlePickLibrary = useCallback(
    async (item: MusicLibraryItem) => {
      setLibraryApplying(item.name);
      try {
        await apiClient.selectLibraryBgm(projectId, item.name);
        // Re-fetch project so ``bgm_path`` reflects the chosen track.
        const proj = await apiClient.fetchProject(projectId);
        onProjectUpdated(proj);
      } catch (err) {
        setLibraryError(
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      } finally {
        setLibraryApplying(null);
      }
    },
    [projectId, onProjectUpdated],
  );

  // ---- AI generation ----
  const [aiPrompt, setAiPrompt] = useState<string>("");
  const [aiPromptLoading, setAiPromptLoading] = useState(false);
  const [aiPromptUsedFallback, setAiPromptUsedFallback] = useState(false);
  const [aiStatus, setAiStatus] = useState<BgmGenerationStatus | null>(null);
  const [aiSubmitting, setAiSubmitting] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

  const loadAiSuggestion = useCallback(async () => {
    setAiPromptLoading(true);
    setAiError(null);
    try {
      const s = await apiClient.fetchMusicSuggestion(projectId);
      setAiPrompt(s.description);
      setAiPromptUsedFallback(s.used_fallback);
    } catch (err) {
      setAiError(err instanceof Error ? err.message : String(err));
    } finally {
      setAiPromptLoading(false);
    }
  }, [projectId]);

  const loadAiStatus = useCallback(async () => {
    try {
      const s = await apiClient.fetchProjectBgmStatus(projectId);
      setAiStatus(s);
    } catch {
      // tolerate — polling will retry
    }
  }, [projectId]);

  useEffect(() => {
    if (source !== "ai") return;
    // Lazy-load suggestion on first switch into the AI tab.
    if (!aiPrompt && !aiPromptLoading) void loadAiSuggestion();
    // Always re-fetch the most recent gen status on mount.
    void loadAiStatus();
  }, [source, aiPrompt, aiPromptLoading, loadAiSuggestion, loadAiStatus]);

  // While a job is pending / running, poll every 4 s. ``aiStatus`` is
  // the source of truth — when it flips to done/failed the interval
  // tears down so we don't hammer the api.
  useEffect(() => {
    if (source !== "ai") return;
    if (
      !aiStatus ||
      (aiStatus.status !== "pending" && aiStatus.status !== "running")
    ) {
      return;
    }
    const handle = window.setInterval(() => {
      void loadAiStatus();
    }, 4000);
    return () => window.clearInterval(handle);
  }, [source, aiStatus, loadAiStatus]);

  // When a fresh gen finishes, refresh the project so ``bgm_path``
  // updates to the AI-generated track.
  const lastSeenStatus = useRef<string | null>(null);
  useEffect(() => {
    const cur = aiStatus?.status ?? null;
    if (cur === "done" && lastSeenStatus.current !== "done") {
      apiClient
        .fetchProject(projectId)
        .then(onProjectUpdated)
        .catch(() => {});
    }
    lastSeenStatus.current = cur;
  }, [aiStatus, projectId, onProjectUpdated]);

  const handleGenerate = useCallback(async () => {
    if (!aiPrompt.trim()) return;
    setAiSubmitting(true);
    setAiError(null);
    try {
      const s = await apiClient.generateProjectBgm(projectId, aiPrompt.trim());
      setAiStatus(s);
    } catch (err) {
      setAiError(
        err instanceof ApiError
          ? `${err.status}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setAiSubmitting(false);
    }
  }, [projectId, aiPrompt]);

  // ---- Upload (unchanged from v0.14.5 BgmUploader) ----
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [uploadInProgress, setUploadInProgress] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const handleUpload = useCallback(
    async (file: File) => {
      setUploadInProgress(true);
      setUploadError(null);
      try {
        const proj = await apiClient.uploadProjectBgm(projectId, file);
        onProjectUpdated(proj);
      } catch (err) {
        setUploadError(
          err instanceof ApiError
            ? `${err.status}: ${err.message}`
            : err instanceof Error
              ? err.message
              : String(err),
        );
      } finally {
        setUploadInProgress(false);
      }
    },
    [projectId, onProjectUpdated],
  );

  return (
    <div className="bgm-picker">
      <div className="bgm-picker__head">
        <span className="bgm-picker__title">配樂來源</span>
        {filename && (
          <span className="bgm-picker__current mono">
            目前：{filename}
          </span>
        )}
      </div>
      <div className="bgm-picker__radios" role="radiogroup">
        {(
          [
            ["none", "不使用配樂"],
            ["library", "從音樂庫選擇"],
            ["ai", "AI 生成配樂"],
            ["upload", "上傳自己的音樂"],
          ] as const
        ).map(([val, label]) => (
          <label
            key={val}
            className={`bgm-picker__radio${source === val ? " bgm-picker__radio--active" : ""}`}
          >
            <input
              type="radio"
              name="bgm-source"
              value={val}
              checked={source === val}
              disabled={disabled}
              onChange={() => setSource(val)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>

      {source === "none" && (
        <p className="bgm-picker__hint mono">
          影片渲染時不混入背景音樂，只保留人聲。
        </p>
      )}

      {source === "library" && (
        <div className="bgm-picker__panel">
          {libraryLoading && (
            <p className="bgm-picker__hint mono">載入音樂庫中…</p>
          )}
          {libraryError && (
            <p className="bgm-picker__err mono" role="alert">
              載入失敗：{libraryError}
            </p>
          )}
          {library && library.length === 0 && (
            <p className="bgm-picker__hint mono">
              音樂庫目前是空的。請執行
              <span className="mono"> scripts/seed_music_library.py </span>
              預生成 5 首風格樣本，或直接把音檔放到
              <span className="mono"> ${"{BGM_DIR}"}/_library/</span>。
            </p>
          )}
          {library && library.length > 0 && (
            <ul className="bgm-library">
              {library.map((item) => (
                <li key={item.url} className="bgm-library__item">
                  <div className="bgm-library__meta">
                    {item.style && (
                      <span className="bgm-library__style">[{item.style}]</span>
                    )}
                    <span className="bgm-library__name">{item.name}</span>
                    {item.duration_s != null && (
                      <span className="bgm-library__dur mono">
                        {item.duration_s.toFixed(0)} 秒
                      </span>
                    )}
                  </div>
                  <audio
                    className="bgm-library__audio"
                    controls
                    preload="none"
                    src={item.url}
                  />
                  <button
                    type="button"
                    className="cta cta--quiet"
                    onClick={() => void handlePickLibrary(item)}
                    disabled={disabled || libraryApplying === item.name}
                  >
                    {libraryApplying === item.name ? "套用中…" : "套用此首"}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {source === "ai" && (
        <div className="bgm-picker__panel">
          <p className="bgm-picker__hint mono">
            AI 會根據素材的場景、運鏡、情緒生成一段風格描述。可手動修改後再生成 30 秒配樂（約需 30–60 秒）。
          </p>
          {aiPromptLoading && (
            <p className="bgm-picker__hint mono">產生建議中…</p>
          )}
          {aiPromptUsedFallback && !aiPromptLoading && (
            <p className="bgm-picker__hint mono">
              （Gemini 暫不可用，已填入預設描述。）
            </p>
          )}
          <textarea
            className="bgm-picker__prompt"
            value={aiPrompt}
            placeholder="例：輕快的 lo-fi 配樂，鋼琴搭配電子節拍，70 BPM，溫暖懷舊。"
            rows={4}
            maxLength={2000}
            disabled={disabled || aiSubmitting}
            onChange={(e) => setAiPrompt(e.currentTarget.value)}
          />
          <div className="bgm-picker__row">
            <button
              type="button"
              className="cta cta--quiet"
              onClick={() => void loadAiSuggestion()}
              disabled={disabled || aiPromptLoading || aiSubmitting}
            >
              重新產生建議
            </button>
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleGenerate()}
              disabled={disabled || !aiPrompt.trim() || aiSubmitting}
            >
              {aiSubmitting ? "排隊中…" : "生成配樂"}
            </button>
          </div>
          {aiStatus && aiStatus.job_id != null && (
            <div className="bgm-picker__status">
              <span className="bgm-picker__status-label mono">
                狀態：{labelForGenStatus(aiStatus.status)}
              </span>
              {aiStatus.status === "done" && aiStatus.output_url && (
                <audio
                  className="bgm-library__audio"
                  controls
                  preload="none"
                  src={aiStatus.output_url}
                />
              )}
              {aiStatus.error && (
                <span className="bgm-picker__err mono">{aiStatus.error}</span>
              )}
            </div>
          )}
          {aiError && (
            <p className="bgm-picker__err mono" role="alert">
              {aiError}
            </p>
          )}
        </div>
      )}

      {source === "upload" && (
        <div className="bgm-picker__panel">
          <input
            ref={fileInputRef}
            type="file"
            accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,audio/*"
            hidden
            onChange={(e) => {
              const f = e.currentTarget.files?.[0];
              if (f) void handleUpload(f);
              e.currentTarget.value = "";
            }}
          />
          <button
            type="button"
            className="cta cta--quiet"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || uploadInProgress}
          >
            {uploadInProgress
              ? "上傳中…"
              : filename
                ? `更換配樂（目前：${filename}）`
                : "選擇音檔"}
          </button>
          <p className="bgm-picker__hint mono">
            支援 mp3 / wav / m4a / aac / flac / ogg；上限 50 MB。配樂會自動與人聲混音並 ducking。
          </p>
          {uploadError && (
            <p className="bgm-picker__err mono" role="alert">
              {uploadError}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
