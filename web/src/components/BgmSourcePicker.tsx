import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type {
  BgmGenerationStatus,
  ClipStylePreset,
  MusicLibraryItem,
  ProjectDetail,
} from "../api/types";
import "./BgmSourcePicker.css";

export type BgmSource = "none" | "library" | "ai" | "upload";
type Source = BgmSource;

// v0.20.2 — copy for the per-preset BGM banner shown above the radios.
// Mirrors the ``bgmHint`` text on each StylePresetCard in ProjectEdit so
// the user sees "風格預設建議" wording in two places without surprises.
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
  // screen. ``custom`` (or omitted) means no preset hint.
  stylePreset?: ClipStylePreset;
  // v0.20.2 — notify the parent every time the user (or auto-switch
  // effect) flips the source radio. Lets ProjectEdit show "目前配樂"
  // in the section title and decorate the style preset card with a
  // strikethrough on its bgm hint when the user manually overrode it.
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
  const [source, setSource] = useState<Source>(() =>
    bgmPath ? "upload" : "none",
  );
  const filename = useMemo(() => bgmFilename(bgmPath), [bgmPath]);

  // v0.20.2 — track whether the user has manually picked a source so
  // (a) we can show a "已被手動覆蓋" pill on the preset banner and
  // (b) the auto-switch effect below knows to leave a manual choice
  // alone even when the user later flips between presets. The flag
  // is sticky once set in this session.
  const userChoseSourceRef = useRef(false);

  const updateSource = useCallback(
    (next: Source, fromUser: boolean) => {
      if (fromUser) userChoseSourceRef.current = true;
      setSource(next);
    },
    [],
  );

  // Notify parent on every source change (initial + subsequent). Plays
  // back into ProjectEdit's section-title summary line so the header
  // always mirrors what the radios show.
  useEffect(() => {
    onSourceChange?.(source);
  }, [source, onSourceChange]);

  // v0.20.2 — auto-switch from "不使用配樂" → "AI 生成配樂" the first
  // time the user picks a non-custom style preset. We use a ref to
  // read the *current* source without putting it in the dep array
  // (otherwise the effect would re-fire on every source flip and
  // immediately reset a manual "none" back to "ai", trapping the user).
  // We also bail out once the user has manually picked anything, so
  // their explicit choice always wins.
  const sourceRef = useRef(source);
  useEffect(() => {
    sourceRef.current = source;
  }, [source]);
  useEffect(() => {
    if (!stylePreset || stylePreset === "custom") return;
    if (userChoseSourceRef.current) return;
    if (sourceRef.current === "none") {
      // fromUser=false: auto-switch must NOT mark the source as
      // user-chosen, otherwise a later real user click never gets
      // the "已覆蓋" banner treatment.
      updateSource("ai", false);
    }
  }, [stylePreset, updateSource]);

  // The banner copy mirrors the style-preset card on ProjectEdit so
  // the suggestion is visible in both places. ``presetActive`` gates
  // the banner — only render when a real preset (not "custom") is on.
  const presetActive =
    stylePreset !== undefined && stylePreset !== "custom";
  const presetBgmHint = presetActive
    ? PRESET_BGM_HINT[stylePreset as Exclude<ClipStylePreset, "custom">]
    : null;
  const presetLabel = presetActive
    ? PRESET_LABEL[stylePreset as Exclude<ClipStylePreset, "custom">]
    : null;
  // The preset's hint is "overridden" once the user has explicitly
  // touched the radios. Show a strikethrough + small "已覆蓋" pill so
  // the user can tell at a glance which one wins.
  const presetOverridden = userChoseSourceRef.current;

  // Single-source-of-truth status line: "目前最終效果" — what will
  // actually be rendered with the current radios. Computed here so
  // it stays in lock-step with all the radio-driven state below.
  const finalStatusLabel = (() => {
    if (source === "none") return "🔇 不使用配樂（影片只有人聲）";
    if (source === "library") {
      if (filename) return `🎵 已套用音樂庫：${filename}`;
      return "🎵 從音樂庫選擇（尚未套用，請於下方挑選）";
    }
    if (source === "ai") {
      if (filename) return `🎼 AI 生成配樂：${filename}`;
      return "🎼 AI 生成配樂（尚未生成，請於下方產生）";
    }
    // upload
    if (filename) return `📁 已上傳：${filename}`;
    return "📁 上傳配樂（尚未選擇檔案，請於下方上傳）";
  })();

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
  // v0.16 — once the user types into the AI prompt textarea, never let
  // a background suggestion fetch overwrite their text. The flag flips
  // back to false ONLY when the user explicitly clicks 「重新產生建議」.
  // Stored in a ref (not useState) so changing it doesn't re-trigger
  // the auto-load effect below.
  const aiPromptUserEditedRef = useRef(false);

  const loadAiSuggestion = useCallback(
    async (forceReplace: boolean) => {
      setAiPromptLoading(true);
      setAiError(null);
      try {
        const s = await apiClient.fetchMusicSuggestion(projectId, stylePreset);
        // Only write into the textarea on the initial fetch (the user
        // hasn't started editing yet) OR when the caller explicitly
        // asked for a replace via the 「重新產生建議」 button. This
        // protects user edits across re-renders, parent state changes,
        // and any future polling-style refresh of the suggestion.
        if (forceReplace || !aiPromptUserEditedRef.current) {
          setAiPrompt(s.description);
          setAiPromptUsedFallback(s.used_fallback);
          if (forceReplace) {
            // After an explicit "regenerate" the textarea now matches
            // the fresh suggestion — drop the edited flag so the user
            // can re-edit and the cycle works again.
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

  useEffect(() => {
    if (source !== "ai") return;
    // Lazy-load suggestion on first switch into the AI tab. Skip when
    // the user has already typed something — even if their edits left
    // the textarea momentarily empty, we treat that as intentional
    // (they're rewriting the prompt) and don't reset to the AI text.
    if (
      !aiPrompt &&
      !aiPromptLoading &&
      !aiPromptUserEditedRef.current
    ) {
      void loadAiSuggestion(false);
    }
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

  // True while a generation job sits on the queue or runs on the
  // worker. Used to keep both AI buttons disabled (and a spinner
  // visible) so the user can't queue a second job by accident — the
  // worker is single-GPU and parallel jobs would just contend.
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

      {/* v0.20.2 — preset banner. Mirrors the BGM hint from the style
          preset card so the user sees the recommendation right next
          to the radios that override it. Greys out + strikes through
          once the user has manually picked a source so it's clear
          which one wins. */}
      {presetActive && presetBgmHint && (
        <div
          className={
            "bgm-picker__preset-banner" +
            (presetOverridden ? " bgm-picker__preset-banner--overridden" : "")
          }
          role="note"
        >
          <span className="bgm-picker__preset-banner-tag mono">
            風格預設「{presetLabel}」
          </span>
          <span className="bgm-picker__preset-banner-hint">
            建議配樂：{presetBgmHint}
          </span>
          {presetOverridden ? (
            <span className="bgm-picker__preset-banner-pill mono">
              已被下方覆蓋
            </span>
          ) : (
            <span className="bgm-picker__preset-banner-pill bgm-picker__preset-banner-pill--info mono">
              建議僅供參考
            </span>
          )}
        </div>
      )}

      {/* "目前最終效果" — single line that always tells the user what
          will actually happen on the next render. Sits above the
          radios so the user sees consequence before mechanism. */}
      <div className="bgm-picker__final" aria-live="polite">
        <span className="bgm-picker__final-label mono">最終效果</span>
        <span className="bgm-picker__final-value">{finalStatusLabel}</span>
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
              onChange={() => updateSource(val, true)}
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
              音樂庫正在準備中，第一批風格樣本即將上線。請改用「AI 生成配樂」或「上傳自己的音樂」。
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
          {filename && (
            <p className="bgm-picker__hint mono">
              目前配樂：<span className="mono">{filename}</span>。重新生成會建立新檔案（generated_<i>{`{時間戳}`}</i>.wav），舊草稿仍會沿用原本的配樂。按下「重新生成」前，目前這首一直保留。
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
              // First keystroke flips the userEdited flag; while set,
              // any background suggestion fetch silently skips the
              // textarea (see ``loadAiSuggestion``). The flag only
              // resets when the user explicitly clicks 「重新產生建議」.
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
