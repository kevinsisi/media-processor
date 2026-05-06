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

// v0.21.2 — short genre tag used in the "配樂已生成" status line so the
// user reads the music *style* rather than the preset's display name
// (e.g. "Acoustic/indie 風格" instead of "文青風 風格"). Trimmed from
// PRESET_BGM_HINT so we don't echo the BPM noise into the status chip.
const PRESET_GENRE_SHORT: Record<Exclude<ClipStylePreset, "custom">, string> = {
  fast: "高能量電子/搖滾",
  slow: "柔和氛圍",
  commercial: "Corporate",
  artistic: "Acoustic/indie",
};

// v0.21.2 — reverse lookup: given the prompt that was sent to MusicGen
// (server returns it on ``BgmGenerationStatus.prompt``), figure out
// which preset's hint produced it. Used to detect "the user's chosen
// style preset has changed since the current BGM was generated" so
// the panel can flag a mismatch instead of silently keeping the stale
// track. Returns ``null`` when the prompt is a free-form one (the
// "AI 自訂生成" path) or doesn't match any known hint exactly.
function presetForPrompt(
  prompt: string | null | undefined,
): Exclude<ClipStylePreset, "custom"> | null {
  if (!prompt) return null;
  for (const [key, hint] of Object.entries(PRESET_BGM_HINT)) {
    if (hint === prompt) {
      return key as Exclude<ClipStylePreset, "custom">;
    }
  }
  return null;
}

// v0.21.2 — does the latest generation status point at the file the
// project currently has set as its bgm_path? When false, the AI job
// completed but the user has since uploaded / picked a library track,
// so the "已根據 X 生成" banner shouldn't fire.
function statusOutputMatchesFilename(
  outputUrl: string | null | undefined,
  filename: string | null,
): boolean {
  if (!outputUrl || !filename) return false;
  return outputUrl.endsWith(filename);
}

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
  if (!status) return "尚未製作";
  if (status === "pending") return "等待開始";
  if (status === "running") return "製作中（約 30–60 秒）";
  if (status === "done") return "已完成";
  if (status.startsWith("failed:")) {
    const reason = status.slice("failed:".length);
    if (reason === "model-unavailable") {
      return "配樂製作失敗，請先改用音樂庫或上傳自己的音樂";
    }
    return "配樂製作失敗，請稍後重試或改用音樂庫";
  }
  return "狀態需確認";
}

function formatBgmSubmitError(err: unknown): string {
  const detail =
    err instanceof ApiError
      ? err.message
      : err instanceof Error
        ? err.message
        : String(err);
  return `配樂送出失敗，請稍後重試或改用音樂庫。錯誤細節：${detail}`;
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
      setAiError(formatBgmSubmitError(err));
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
      setAiError(formatBgmSubmitError(err));
    } finally {
      setAiSubmitting(false);
    }
  }, [projectId, presetHint]);

  // ---- v0.21.4 — preset-match derived signals + auto-trigger ----
  //
  // Lifted out of the JSX IIFE so the auto-trigger useEffect below
  // can read them. ``presetMatches`` is the "current BGM is in sync
  // with the active style preset" state; ``presetMismatch`` is the
  // "BGM is from a different preset"; ``bgmIsExternal`` covers the
  // upload / library cases. All three drive both the banner copy and
  // whether auto-trigger fires.
  const lastGenPreset = useMemo(
    () => presetForPrompt(aiStatus?.prompt),
    [aiStatus?.prompt],
  );
  const aiOutputIsCurrent = useMemo(
    () => statusOutputMatchesFilename(aiStatus?.output_url, filename),
    [aiStatus?.output_url, filename],
  );
  const presetMatches =
    filename != null
    && aiStatus?.status === "done"
    && aiOutputIsCurrent
    && lastGenPreset === presetKey;
  const presetMismatch =
    filename != null
    && aiStatus?.status === "done"
    && aiOutputIsCurrent
    && lastGenPreset != null
    && lastGenPreset !== presetKey;
  const bgmIsExternal =
    filename != null
    && !presetMatches
    && !presetMismatch
    && aiStatus?.status !== "pending"
    && aiStatus?.status !== "running";

  // Auto-trigger MusicGen when the user picks the "preset" source
  // (or switches the project's style preset while already on
  // "preset") and the current BGM doesn't already match. Saves the
  // user one click in the most common path: pick style → pick
  // "依風格預設自動生成" → 30 seconds later, BGM is ready. Manual
  // re-rolls (the "🔄 換一首" button) bypass this since they call
  // ``handleGeneratePreset`` directly; this effect just covers the
  // implicit case.
  //
  // ``autoTriggeredFor`` tracks the (source, presetKey) combo we've
  // already fired once for, so a re-render or a status flip doesn't
  // loop us. Switching presets changes the combo and re-arms a new
  // auto-fire. Switching away from "preset" and back also re-arms.
  const autoTriggeredFor = useRef<string | null>(null);
  useEffect(() => {
    if (source !== "preset") {
      // Reset the latch so re-entering "preset" can fire once again.
      autoTriggeredFor.current = null;
      return;
    }
    if (!presetActive || !presetKey || !presetHint) return;
    // Wait for the first ``aiStatus`` fetch to finish — without that
    // we'd false-trigger on existing matching BGM (we'd see ``null``
    // status and assume "no BGM yet").
    if (aiStatus === null) return;
    // Don't double-fire while a job is queued / running / submitting.
    if (aiSubmitting || aiJobInFlight) return;
    // BGM is already in sync with the active preset — nothing to do.
    if (presetMatches) return;
    // Only auto-fire once per (source, presetKey) combo. The user
    // can still hit the "換一首" button manually for variety; that
    // path bypasses this latch.
    const combo = `preset|${presetKey}`;
    if (autoTriggeredFor.current === combo) return;
    autoTriggeredFor.current = combo;
    void handleGeneratePreset();
  }, [
    source,
    presetActive,
    presetKey,
    presetHint,
    aiStatus,
    aiSubmitting,
    aiJobInFlight,
    presetMatches,
    handleGeneratePreset,
  ]);

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
            ["preset", "依影片風格自動配樂"],
            ["library", "從音樂庫選擇"],
            ["ai", "描述想要的配樂"],
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
                  ? "請先在「影片風格」選擇非「自訂」的風格"
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
          產生成品時不加入背景音樂，只保留原聲與人聲。
        </p>
      )}

      {source === "preset" && presetKey && (() => {
        // v0.21.4 — derived match/mismatch signals are lifted to
        // component scope (so the auto-trigger useEffect can read
        // them); only the small render-only locals stay here.
        const isBusy = aiSubmitting || aiJobInFlight;
        const regenLabel = isBusy
          ? null
          : filename
            ? "🔄 重新製作配樂"
            : "🎵 製作 30 秒配樂";

        return (
          <div className="bgm-picker__panel">
            <p className="bgm-picker__hint mono">
              配樂風格描述（系統會依這段文字製作配樂）：
            </p>
            <div className="bgm-picker__preset-readonly mono">
              <span className="bgm-picker__preset-readonly-tag">
                「{presetLabel}」
              </span>
              <span className="bgm-picker__preset-readonly-hint">
                {presetHint}
              </span>
            </div>

            {presetMatches && (
              <div className="bgm-picker__status-banner bgm-picker__status-banner--match">
                <span aria-hidden="true">✓</span>
                <span>已根據「{presetLabel}」製作配樂</span>
              </div>
            )}
            {presetMismatch && lastGenPreset && (
              <div className="bgm-picker__status-banner bgm-picker__status-banner--mismatch">
                <div className="bgm-picker__status-banner-head">
                  <span
                    className="bgm-picker__status-banner-icon"
                    aria-hidden="true"
                  >
                    ⚠
                  </span>
                  <strong className="bgm-picker__status-banner-title">
                    配樂尚未更新！目前播放的仍是舊配樂
                  </strong>
                </div>
                <p className="bgm-picker__status-banner-body">
                  風格已從「{PRESET_LABEL[lastGenPreset]}」改為「{presetLabel}」，
                  但配樂仍是「{PRESET_LABEL[lastGenPreset]}」
                  （{PRESET_GENRE_SHORT[lastGenPreset]}）的版本。請按下方
                  <strong>「重新製作配樂」</strong>套用新風格。
                </p>
              </div>
            )}
            {bgmIsExternal && (
              <p className="bgm-picker__hint mono">
                目前配樂為自行上傳或音樂庫選曲（{filename}）。要使用「
                {presetLabel}」風格的話，按下方重新製作。
              </p>
            )}

            <div className="bgm-picker__row">
              {presetMatches && !isBusy ? (
                // Match → quiet link, lets the user re-roll the
                // current preset for variety. v0.21.4 — labelled
                // "換一首" since auto-trigger handles the initial
                // generation; this button is now purely "MusicGen
                // is non-deterministic, give me a different take".
                <button
                  type="button"
                  className="bgm-picker__regen-link"
                  onClick={() => void handleGeneratePreset()}
                  disabled={disabled}
                  title="每次製作都會有不同結果，按一下換另一個版本"
                >
                  🔄 換一首
                </button>
              ) : (
                <button
                  type="button"
                  className={
                    presetMismatch
                      ? "cta cta--primary bgm-picker__regen-cta--loud"
                      : "cta cta--primary"
                  }
                  onClick={() => void handleGeneratePreset()}
                  disabled={disabled || isBusy}
                >
                  {isBusy ? (
                    <span className="cta__spinner-row">
                      <span
                        className="bgm-picker__spinner"
                        aria-hidden="true"
                      />
                      {aiSubmitting
                        ? "等待開始…"
                        : aiStatus?.status === "running"
                          ? "製作中…"
                          : "等待開始…"}
                    </span>
                  ) : (
                    regenLabel
                  )}
                </button>
              )}
            </div>

            {aiStatus && aiStatus.job_id != null && (
              <div className="bgm-picker__status">
                {aiStatus.status !== "done" && (
                  <span className="bgm-picker__status-label mono">
                    {labelForGenStatus(aiStatus.status)}
                  </span>
                )}
                {aiStatus.status === "done" && (
                  <span className="bgm-picker__status-label mono">
                    {presetMismatch && lastGenPreset
                      ? `🕘 舊版本：${PRESET_GENRE_SHORT[lastGenPreset]} 風格（按上方重新製作才會更新）`
                      : lastGenPreset
                        ? `配樂已製作（${PRESET_GENRE_SHORT[lastGenPreset]} 風格）`
                        : "配樂已製作（自訂描述）"}
                  </span>
                )}
                {aiStatus.status === "done" && aiStatus.output_url && (
                  <audio
                    className={
                      presetMismatch
                        ? "bgm-library__audio bgm-picker__audio--stale"
                        : "bgm-library__audio"
                    }
                    controls
                    preload="none"
                    src={aiStatus.output_url}
                  />
                )}
                {aiStatus.error && (
                  <span className="bgm-picker__err mono">
                    錯誤細節：{aiStatus.error}
                  </span>
                )}
              </div>
            )}
            {aiError && (
              <p className="bgm-picker__err mono" role="alert">
                {aiError}
              </p>
            )}
          </div>
        );
      })()}

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
              音樂庫正在準備中，第一批風格樣本即將上線。請改用「描述想要的配樂」或「上傳自己的音樂」。
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
            自行描述音樂風格，系統會製作 30 秒配樂（約需 30–60 秒）。
          </p>
          {filename && (
            <p className="bgm-picker__hint mono">
              目前配樂：<span className="mono">{filename}</span>。重新製作會建立新檔案，舊版本仍會沿用原本的配樂。
            </p>
          )}
          {aiPromptLoading && (
            <p className="bgm-picker__hint mono">產生建議中…</p>
          )}
          {aiPromptUsedFallback && !aiPromptLoading && (
            <p className="bgm-picker__hint mono">
              （AI 建議暫不可用，已填入預設描述。）
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
                    ? "等待開始…"
                    : aiStatus?.status === "running"
                      ? "製作中…"
                      : "等待開始…"}
                </span>
              ) : filename ? (
                "重新製作（更換舊配樂）"
              ) : (
                "🎵 製作 30 秒配樂"
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
                <span className="bgm-picker__err mono">
                  錯誤細節：{aiStatus.error}
                </span>
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
            支援 mp3 / wav / m4a / aac / flac / ogg；上限 50 MB。配樂會自動與人聲混合，說話時會自動降低音量。
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
