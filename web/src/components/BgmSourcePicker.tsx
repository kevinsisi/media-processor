import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type {
  BgmGenerationStatus,
  ClipStylePreset,
  MusicLibraryItem,
  ProjectDetail,
} from "../api/types";
import "./BgmSourcePicker.css";

// v0.20.3 — single 5-radio picker. Each radio is the FINAL outcome —
// no "suggestion" layer, no "preset banner", no "最終效果" recap.
// "preset" = AI generates with the current style preset's description
// as the MusicGen prompt; "ai" = user types a free-form prompt.
export type BgmSource = "none" | "preset" | "library" | "ai" | "upload";
type Source = BgmSource;

// Mirrors the bgmHint on each StylePresetCard in ProjectEdit. Shown in
// the "preset" panel so the user knows exactly what description will be
// sent to MusicGen.
const PRESET_BGM_HINT: Record<Exclude<ClipStylePreset, "custom">, string> = {
  fast: "高能量電子 / 搖滾 130–150 BPM",
  slow: "柔和氛圍音 60–80 BPM",
  commercial: "Corporate 配樂 90–110 BPM",
  artistic: "Acoustic / indie 木吉他 80–100 BPM",
};

const PRESET_LABEL: Record<Exclude<ClipStylePreset, "custom">, string> = {
  fast: "快節奏",
  slow: "慢節奏",
  commercial: "商業感",
  artistic: "文青風",
};

interface BgmSourcePickerProps {
  projectId: number;
  bgmPath: string | null | undefined;
  onProjectUpdated: (project: ProjectDetail) => void;
  disabled?: boolean;
  // v0.18 — when set, the music-suggestion API is called with this
  // preset so the suggested BGM matches the rhythm picked on the edit
  // screen. ``custom`` (or omitted) disables the "preset" radio.
  stylePreset?: ClipStylePreset;
  // v0.20.2 — notify the parent every time the user (or auto-switch
  // effect) flips the source radio. Lets ProjectEdit show the current
  // source in the section title.
  onSourceChange?: (source: BgmSource) => void;
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
  stylePreset,
  onSourceChange,
}: BgmSourcePickerProps) {
  const presetActive =
    stylePreset !== undefined && stylePreset !== "custom";
  const presetKey = presetActive
    ? (stylePreset as Exclude<ClipStylePreset, "custom">)
    : null;
  const presetHint = presetKey ? PRESET_BGM_HINT[presetKey] : null;
  const presetLabel = presetKey ? PRESET_LABEL[presetKey] : null;

  // Initial source: an existing uploaded/AI track lands on "upload"
  // (the user can re-pick a different source from there); otherwise
  // start at "none". The auto-switch effect below moves "none" → "preset"
  // when a non-custom style preset is in play and the user hasn't yet
  // manually picked anything.
  const [source, setSource] = useState<Source>(() =>
    bgmPath ? "upload" : "none",
  );
  const filename = useMemo(() => bgmFilename(bgmPath), [bgmPath]);

  // Sticky once-set: any explicit user click on a radio flips this so
  // the auto-switch effect leaves manual choices alone, even on later
  // style-preset flips.
  const userChoseSourceRef = useRef(false);

  const updateSource = useCallback((next: Source, fromUser: boolean) => {
    if (fromUser) userChoseSourceRef.current = true;
    setSource(next);
  }, []);

  // Notify parent on every source change so ProjectEdit's section
  // header summary stays in sync.
  useEffect(() => {
    onSourceChange?.(source);
  }, [source, onSourceChange]);

  // Auto-switch from "none" → "preset" the first time the user picks a
  // non-custom style preset. Read the *current* source via ref so the
  // effect doesn't re-fire on every source flip and trap the user in
  // a manual "none" they just chose.
  const sourceRef = useRef(source);
  useEffect(() => {
    sourceRef.current = source;
  }, [source]);
  useEffect(() => {
    if (!presetActive) return;
    if (userChoseSourceRef.current) return;
    if (sourceRef.current === "none") {
      updateSource("preset", false);
    }
  }, [presetActive, updateSource]);

  // If the user is sitting on "preset" and then switches the style
  // preset back to "custom", "preset" is no longer a valid choice.
  // Fall back to "none" so the radio doesn't show a disabled-but-
  // selected state.
  useEffect(() => {
    if (source === "preset" && !presetActive) {
      updateSource("none", false);
    }
  }, [presetActive, source, updateSource]);

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

  // ---- AI generation (shared by "preset" and "ai" sources) ----
  const [aiPrompt, setAiPrompt] = useState<string>("");
  const [aiPromptLoading, setAiPromptLoading] = useState(false);
  const [aiPromptUsedFallback, setAiPromptUsedFallback] = useState(false);
  const [aiStatus, setAiStatus] = useState<BgmGenerationStatus | null>(null);
  const [aiSubmitting, setAiSubmitting] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  // Once the user types into the AI prompt textarea, never let a
  // background suggestion fetch overwrite their text. Flips back to
  // false ONLY when they click "重新產生建議".
  const aiPromptUserEditedRef = useRef(false);

  const loadAiSuggestion = useCallback(
    async (forceReplace: boolean) => {
      setAiPromptLoading(true);
      setAiError(null);
      try {
        const s = await apiClient.fetchMusicSuggestion(projectId, stylePreset);
        if (forceReplace || !aiPromptUserEditedRef.current) {
          setAiPrompt(s.description);
          setAiPromptUsedFallback(s.used_fallback);
          if (forceReplace) {
            aiPromptUserEditedRef.current = false;
          }
        }
      } catch (err) {
        setAiError(err instanceof Error ? err.message : String(err));
      } finally {
        setAiPromptLoading(false);
      }
    },
    [projectId, stylePreset],
  );

  const loadAiStatus = useCallback(async () => {
    try {
      const s = await apiClient.fetchProjectBgmStatus(projectId);
      setAiStatus(s);
    } catch {
      // tolerate — polling will retry
    }
  }, [projectId]);

  // Lazy-load suggestion on first switch into "ai". For "preset" the
  // prompt is the static preset hint; no suggestion fetch needed.
  useEffect(() => {
    if (source !== "ai") return;
    if (
      !aiPrompt &&
      !aiPromptLoading &&
      !aiPromptUserEditedRef.current
    ) {
      void loadAiSuggestion(false);
    }
    void loadAiStatus();
  }, [source, aiPrompt, aiPromptLoading, loadAiSuggestion, loadAiStatus]);

  // For "preset", just keep the gen status fresh so the user sees
  // pending/running/done updates if they had a job in flight.
  useEffect(() => {
    if (source !== "preset") return;
    void loadAiStatus();
  }, [source, loadAiStatus]);

  useEffect(() => {
    if (source !== "ai" && source !== "preset") return;
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

  const aiJobInFlight =
    aiStatus?.status === "pending" || aiStatus?.status === "running";

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

  // For "preset": send the preset hint string verbatim as the prompt.
  // No textarea, no editing — the radio choice IS the prompt.
  const handleGeneratePreset = useCallback(async () => {
    if (!presetHint) return;
    setAiSubmitting(true);
    setAiError(null);
    try {
      const s = await apiClient.generateProjectBgm(projectId, presetHint);
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
  }, [projectId, presetHint]);

  // ---- Upload ----
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

  // The "preset" radio is only meaningful when a non-custom preset is
  // selected upstream. When custom (or undefined), it's disabled with
  // a hint telling the user where to enable it.
  const presetRadioDisabled = disabled || !presetActive;

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
            ["preset", "依風格預設自動生成"],
            ["library", "從音樂庫選擇"],
            ["ai", "AI 自訂生成"],
            ["upload", "上傳自己的音樂"],
          ] as const
        ).map(([val, label]) => {
          const isPreset = val === "preset";
          const radioDisabled = isPreset ? presetRadioDisabled : disabled;
          return (
            <label
              key={val}
              className={
                `bgm-picker__radio${source === val ? " bgm-picker__radio--active" : ""}` +
                (radioDisabled ? " bgm-picker__radio--disabled" : "")
              }
              title={
                isPreset && !presetActive
                  ? "請先在「剪輯風格預設」選擇非「自訂」的風格"
                  : undefined
              }
            >
              <input
                type="radio"
                name="bgm-source"
                value={val}
                checked={source === val}
                disabled={radioDisabled}
                onChange={() => updateSource(val, true)}
              />
              <span>{label}</span>
            </label>
          );
        })}
      </div>

      {source === "none" && (
        <p className="bgm-picker__hint mono">
          影片渲染時不混入背景音樂，只保留人聲。
        </p>
      )}

      {source === "preset" && presetKey && (
        <div className="bgm-picker__panel">
          <p className="bgm-picker__hint mono">
            風格描述（將直接送給 MusicGen 作為提示詞）：
          </p>
          <div className="bgm-picker__preset-readonly mono">
            <span className="bgm-picker__preset-readonly-tag">
              「{presetLabel}」
            </span>
            <span className="bgm-picker__preset-readonly-hint">
              {presetHint}
            </span>
          </div>
          {filename && (
            <p className="bgm-picker__hint mono">
              目前配樂：<span className="mono">{filename}</span>。重新生成會建立新檔案，舊草稿仍會沿用原本的配樂。
            </p>
          )}
          <div className="bgm-picker__row">
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleGeneratePreset()}
              disabled={disabled || aiSubmitting || aiJobInFlight}
            >
              {aiSubmitting || aiJobInFlight ? (
                <span className="cta__spinner-row">
                  <span className="bgm-picker__spinner" aria-hidden="true" />
                  {aiSubmitting
                    ? "排隊中…"
                    : aiStatus?.status === "running"
                      ? "生成中…"
                      : "排隊中…"}
                </span>
              ) : filename ? (
                "重新生成"
              ) : (
                "生成 30 秒配樂"
              )}
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
              音樂庫正在準備中，第一批風格樣本即將上線。請改用「AI 自訂生成」或「上傳自己的音樂」。
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
            自行描述音樂風格送給 MusicGen 生成 30 秒配樂（約需 30–60 秒）。
          </p>
          {filename && (
            <p className="bgm-picker__hint mono">
              目前配樂：<span className="mono">{filename}</span>。重新生成會建立新檔案，舊草稿仍會沿用原本的配樂。
            </p>
          )}
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
            disabled={disabled || aiSubmitting || aiJobInFlight}
            onChange={(e) => {
              aiPromptUserEditedRef.current = true;
              setAiPrompt(e.currentTarget.value);
            }}
          />
          <div className="bgm-picker__row">
            <button
              type="button"
              className="cta cta--quiet"
              onClick={() => void loadAiSuggestion(true)}
              disabled={disabled || aiPromptLoading || aiSubmitting || aiJobInFlight}
            >
              {aiPromptLoading ? (
                <span className="cta__spinner-row">
                  <span className="bgm-picker__spinner" aria-hidden="true" />
                  產生建議中…
                </span>
              ) : (
                "重新產生建議"
              )}
            </button>
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleGenerate()}
              disabled={
                disabled || !aiPrompt.trim() || aiSubmitting || aiJobInFlight
              }
            >
              {aiSubmitting || aiJobInFlight ? (
                <span className="cta__spinner-row">
                  <span className="bgm-picker__spinner" aria-hidden="true" />
                  {aiSubmitting
                    ? "排隊中…"
                    : aiStatus?.status === "running"
                      ? "生成中…"
                      : "排隊中…"}
                </span>
              ) : filename ? (
                "重新生成"
              ) : (
                "生成配樂"
              )}
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
