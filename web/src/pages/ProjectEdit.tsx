import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, apiClient } from "../api/client";
import BgmSourcePicker from "../components/BgmSourcePicker";
import type { BgmSource } from "../components/BgmSourcePicker";
import DraggableTimeline from "../components/DraggableTimeline";
import ExportSheet from "../components/ExportSheet";
import SubtitleEditor from "../components/SubtitleEditor";
import SubtitleStyleEditor from "../components/SubtitleStyleEditor";
import WatermarkPicker from "../components/WatermarkPicker";
import type {
  ClipStylePreset,
  DraftComment,
  DraftDetail,
  DraftSummary,
  ProjectDetail,
  SubtitlePosition,
  SubtitleSize,
} from "../api/types";
import { useDraftPolling } from "../hooks/useDraftPolling";
import {
  EDIT_STEP_LABELS,
  labelForDraftStatus,
  labelForStepState,
} from "../i18n/tags";
import "./ProjectEdit.css";

const EDIT_STEP_ORDER: (
  | "plan"
  | "cut"
  | "stabilize"
  | "concat"
  | "subtitles"
  | "bgm"
)[] = ["plan", "cut", "stabilize", "concat", "subtitles", "bgm"];

// Quick-pick lengths offered alongside the free-form input. Matches the
// IG/TikTok short-form sweet spots; backend clamps the final value to
// the 10–300 s range regardless of what's typed.
const DURATION_PRESETS_S = [30, 60, 90, 120] as const;
const DEFAULT_DURATION_S = 60;
const DURATION_MIN_S = 10;
const DURATION_MAX_S = 300;

function classifyStepState(value: string | undefined): string {
  if (!value) return "pending";
  if (value.startsWith("failed:")) return "failed";
  return value;
}

// Comment author defaults to the value the user last typed; persists in
// localStorage so reload keeps the same name. Falls back to "我" so a fresh
// session has something usable.
const COMMENT_AUTHOR_KEY = "media-processor:comment-author";
const COMMENT_POLL_MS = 15_000;

function loadCommentAuthor(): string {
  try {
    const v = window.localStorage.getItem(COMMENT_AUTHOR_KEY);
    if (v && v.trim()) return v.trim();
  } catch {
    /* localStorage disabled — fall through */
  }
  return "我";
}

function formatCommentTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const now = Date.now();
  const diffMs = now - d.getTime();
  if (diffMs < 60_000) return "剛剛";
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)} 分鐘前`;
  const sameDay =
    new Date().toDateString() === d.toDateString();
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  if (sameDay) return `${hh}:${mi}`;
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${mm}/${dd} ${hh}:${mi}`;
}

interface DraftCommentsProps {
  draftId: number;
}

function DraftComments({ draftId }: DraftCommentsProps) {
  const [comments, setComments] = useState<DraftComment[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [author, setAuthor] = useState<string>(() => loadCommentAuthor());
  const [body, setBody] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const fetchOnce = useCallback(async () => {
    try {
      const list = await apiClient.fetchDraftComments(draftId);
      setComments(list);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [draftId]);

  useEffect(() => {
    setLoading(true);
    void fetchOnce();
    const handle = window.setInterval(() => {
      void fetchOnce();
    }, COMMENT_POLL_MS);
    return () => window.clearInterval(handle);
  }, [fetchOnce]);

  const submit = useCallback(
    async (ev: React.FormEvent<HTMLFormElement>) => {
      ev.preventDefault();
      const trimmedAuthor = author.trim();
      const trimmedBody = body.trim();
      if (!trimmedAuthor || !trimmedBody) return;
      setSubmitting(true);
      setSubmitError(null);
      try {
        const created = await apiClient.postDraftComment(draftId, {
          author: trimmedAuthor,
          body: trimmedBody,
        });
        setComments((prev) => [...prev, created]);
        setBody("");
        try {
          window.localStorage.setItem(COMMENT_AUTHOR_KEY, trimmedAuthor);
        } catch {
          /* ignore */
        }
      } catch (err) {
        setSubmitError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [author, body, draftId],
  );

  return (
    <section className="draft-comments" aria-label="本版本留言">
      <header className="draft-comments__head">
        <h3 className="draft-comments__title">留言 / 討論</h3>
        <span className="draft-comments__count mono">
          {comments.length} 則
        </span>
      </header>

      {loadError && (
        <p className="edit-error" role="alert">
          載入留言失敗：{loadError}
        </p>
      )}

      {!loading && comments.length === 0 && (
        <p className="draft-comments__empty mono">
          這個版本還沒有留言，先寫第一則。
        </p>
      )}

      <ol className="draft-comments__list">
        {comments.map((c) => (
          <li key={c.id} className="comment-item">
            <div className="comment-item__head">
              <span className="comment-item__author">{c.author}</span>
              <span className="comment-item__time mono">
                {formatCommentTime(c.created_at)}
              </span>
            </div>
            <p className="comment-item__body">{c.body}</p>
          </li>
        ))}
      </ol>

      <form className="draft-comments__form" onSubmit={submit}>
        <div className="draft-comments__form-row">
          <label className="draft-comments__author-label">
            <span className="mono">名字</span>
            <input
              type="text"
              className="draft-comments__author-input"
              value={author}
              maxLength={64}
              onChange={(e) => setAuthor(e.currentTarget.value)}
              disabled={submitting}
            />
          </label>
        </div>
        <textarea
          className="draft-comments__body"
          value={body}
          placeholder="告訴 AI 下次怎麼改進這個版本（例：「不要轉場特效」「蚊子館重複太多」「片頭再有力一點」）。下次重新剪輯時，這裡的留言會餵給 Gemini 作為改進指引。"
          rows={3}
          maxLength={4000}
          onChange={(e) => setBody(e.currentTarget.value)}
          disabled={submitting}
        />
        <div className="draft-comments__form-actions">
          {submitError && (
            <span className="draft-comments__form-err mono" role="alert">
              {submitError}
            </span>
          )}
          <button
            type="submit"
            className="cta cta--primary"
            disabled={submitting || !author.trim() || !body.trim()}
          >
            {submitting ? "送出中…" : "送出留言"}
          </button>
        </div>
      </form>
    </section>
  );
}

interface VersionSwitcherProps {
  drafts: DraftSummary[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  disabled?: boolean;
}

function VersionSwitcher({
  drafts,
  selectedId,
  onSelect,
  disabled,
}: VersionSwitcherProps) {
  if (drafts.length === 0) return null;
  return (
    <nav className="version-switcher" aria-label="剪輯版本">
      <span className="version-switcher__label">版本</span>
      <div className="version-switcher__chips" role="tablist">
        {drafts.map((d) => {
          const isActive = d.id === selectedId;
          return (
            <button
              key={d.id}
              type="button"
              role="tab"
              aria-selected={isActive}
              className={`version-chip version-chip--${d.status}${isActive ? " version-chip--active" : ""}`}
              onClick={() => onSelect(d.id)}
              disabled={disabled}
              title={`v${d.version} · ${labelForDraftStatus(d.status)}`}
            >
              <span className="version-chip__num mono">v{d.version}</span>
              <span className="version-chip__state mono">
                {labelForDraftStatus(d.status)}
              </span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}

interface DurationPickerProps {
  value: number;
  onChange: (next: number) => void;
  disabled?: boolean;
}

function DurationPicker({ value, onChange, disabled }: DurationPickerProps) {
  return (
    <div className="duration-picker" aria-label="目標成品長度">
      <span className="duration-picker__label">目標長度</span>
      <div className="duration-picker__presets">
        {DURATION_PRESETS_S.map((sec) => (
          <button
            key={sec}
            type="button"
            className={`duration-chip${value === sec ? " duration-chip--active" : ""}`}
            onClick={() => onChange(sec)}
            disabled={disabled}
          >
            {sec}s
          </button>
        ))}
      </div>
      <label className="duration-picker__custom">
        <input
          type="number"
          min={DURATION_MIN_S}
          max={DURATION_MAX_S}
          step={1}
          value={value}
          disabled={disabled}
          onChange={(e) => {
            const raw = Number(e.currentTarget.value);
            if (Number.isFinite(raw)) onChange(Math.round(raw));
          }}
        />
        <span className="duration-picker__unit mono">秒</span>
      </label>
      <p className="duration-picker__hint mono">
        範圍 {DURATION_MIN_S}–{DURATION_MAX_S} 秒；超出會被自動修正。
      </p>
    </div>
  );
}

// v0.18 — clip-style preset picker. Each card maps to a backend
// StylePresetParams bundle: span bounds, transition allowlist, BGM
// hint. The fifth "custom" card keeps the legacy free-form behaviour
// (no preset applied; planner uses its defaults).
interface StylePresetCard {
  value: ClipStylePreset;
  label: string;
  icon: string;
  spanHint: string;
  transitionHint: string;
  bgmHint: string;
}

const STYLE_PRESET_CARDS: readonly StylePresetCard[] = [
  {
    value: "fast",
    label: "快節奏",
    icon: "⚡",
    spanHint: "片段 3–5 秒",
    transitionHint: "wipe / slide / circle",
    bgmHint: "高能量電子 / 搖滾 130–150 BPM",
  },
  {
    value: "slow",
    label: "慢節奏",
    icon: "🌊",
    spanHint: "片段 8–15 秒",
    transitionHint: "dissolve / fade",
    bgmHint: "柔和氛圍音 60–80 BPM",
  },
  {
    value: "commercial",
    label: "商業感",
    icon: "🏷️",
    spanHint: "片段 5–8 秒",
    transitionHint: "slide / wipe / fade-black",
    bgmHint: "Corporate 配樂 90–110 BPM",
  },
  {
    value: "artistic",
    label: "文青風",
    icon: "🎨",
    spanHint: "片段 3–12 秒（不規則）",
    transitionHint: "fade / fade-white",
    bgmHint: "Acoustic / indie 木吉他 80–100 BPM",
  },
  {
    value: "custom",
    label: "自訂",
    icon: "✦",
    spanHint: "由 AI 自由決定",
    transitionHint: "依素材內容挑選",
    bgmHint: "依素材內容建議",
  },
] as const;

interface StylePresetPickerProps {
  value: ClipStylePreset;
  onChange: (next: ClipStylePreset) => void;
  disabled?: boolean;
}

function StylePresetPicker({
  value,
  onChange,
  disabled,
}: StylePresetPickerProps) {
  return (
    <fieldset className="style-preset-picker" disabled={disabled}>
      <legend className="style-preset-picker__legend">剪輯風格預設</legend>
      <p className="style-preset-picker__hint mono">
        一鍵切換片段長度、轉場類型與配樂風格建議。
      </p>
      <div
        className="style-preset-picker__grid"
        role="radiogroup"
        aria-label="剪輯風格預設"
      >
        {STYLE_PRESET_CARDS.map((card) => {
          const selected = card.value === value;
          return (
            <button
              key={card.value}
              type="button"
              role="radio"
              aria-checked={selected}
              className={
                "style-preset-card" +
                (selected ? " style-preset-card--selected" : "")
              }
              disabled={disabled}
              onClick={() => onChange(card.value)}
            >
              <span className="style-preset-card__icon" aria-hidden>
                {card.icon}
              </span>
              <span className="style-preset-card__label">{card.label}</span>
              <span className="style-preset-card__hint mono">
                {card.spanHint}
                <br />
                轉場：{card.transitionHint}
                <br />
                配樂：{card.bgmHint}
              </span>
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

interface EditOptionToggleProps {
  label: string;
  hint: string;
  value: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
}

function EditOptionToggle({
  label,
  hint,
  value,
  onChange,
  disabled,
}: EditOptionToggleProps) {
  return (
    <label className="stabilize-toggle">
      <input
        type="checkbox"
        checked={value}
        disabled={disabled}
        onChange={(e) => onChange(e.currentTarget.checked)}
      />
      <span className="stabilize-toggle__label">{label}</span>
      <span className="stabilize-toggle__hint mono">{hint}</span>
    </label>
  );
}

interface RenderOptionsProps {
  stabilize: boolean;
  setStabilize: (v: boolean) => void;
  subtitlesOn: boolean;
  setSubtitlesOn: (v: boolean) => void;
  transitionsOn: boolean;
  setTransitionsOn: (v: boolean) => void;
  autoReframe: boolean;
  setAutoReframe: (v: boolean) => void;
  disabled?: boolean;
}

function RenderOptions({
  stabilize,
  setStabilize,
  subtitlesOn,
  setSubtitlesOn,
  transitionsOn,
  setTransitionsOn,
  autoReframe,
  setAutoReframe,
  disabled,
}: RenderOptionsProps) {
  return (
    <div className="render-options">
      <EditOptionToggle
        label="數位防抖（兩階段 vidstab）"
        hint="手機 / 手持鏡頭建議開啟；腳架或穩定器拍攝可關閉以縮短渲染時間。"
        value={stabilize}
        onChange={setStabilize}
        disabled={disabled}
      />
      <EditOptionToggle
        label="字幕燒入"
        hint="關閉後不產生字幕也不燒進影片。"
        value={subtitlesOn}
        onChange={setSubtitlesOn}
        disabled={disabled}
      />
      <EditOptionToggle
        label="使用轉場效果"
        hint="打勾 = 片段之間加轉場（wipe / slide / circlecrop）；不勾 = 直接硬切。"
        value={transitionsOn}
        onChange={setTransitionsOn}
        disabled={disabled}
      />
      <EditOptionToggle
        label="自動構圖（YOLO 物件追蹤）"
        hint="開啟後 9:16 / 4:5 裁切會自動跟隨主體（人 / 車 / 動物）。素材沒跑過追蹤分析則自動退回置中裁切。"
        value={autoReframe}
        onChange={setAutoReframe}
        disabled={disabled}
      />
    </div>
  );
}

// v0.20.2 — small text helpers for the sub-card status summaries.
// Each setting group's heading shows a one-line digest of its current
// state so the user can audit the configuration at a glance without
// scrolling into every sub-section.

const STYLE_PRESET_LABELS: Record<ClipStylePreset, string> = {
  fast: "快節奏",
  slow: "慢節奏",
  commercial: "商業感",
  artistic: "文青風",
  custom: "自訂",
};

function formatBasicSummary(opts: {
  durationSec: number;
  stylePreset: ClipStylePreset;
  stabilize: boolean;
  subtitlesOn: boolean;
  transitionsOn: boolean;
  autoReframe: boolean;
}): string {
  const flags: string[] = [];
  if (opts.subtitlesOn) flags.push("字幕");
  if (opts.transitionsOn) flags.push("轉場");
  if (opts.autoReframe) flags.push("自動構圖");
  if (opts.stabilize) flags.push("防抖");
  const flagText = flags.length > 0 ? flags.join(" / ") : "純硬切";
  return `${opts.durationSec} 秒 · ${STYLE_PRESET_LABELS[opts.stylePreset]} · ${flagText}`;
}

function formatBgmSummary(opts: {
  source: BgmSource;
  bgmFilename: string | null;
}): string {
  switch (opts.source) {
    case "none":
      return "🔇 不使用配樂";
    case "preset":
      return opts.bgmFilename
        ? `🎼 風格預設配樂：${opts.bgmFilename}`
        : "🎼 依風格預設自動生成（待產生）";
    case "library":
      return opts.bgmFilename
        ? `🎵 音樂庫：${opts.bgmFilename}`
        : "🎵 從音樂庫選擇（待挑選）";
    case "ai":
      return opts.bgmFilename
        ? `🎼 AI 自訂配樂：${opts.bgmFilename}`
        : "🎼 AI 自訂生成（待產生）";
    case "upload":
      return opts.bgmFilename
        ? `📁 已上傳：${opts.bgmFilename}`
        : "📁 自行上傳（待選檔）";
  }
}

function formatVisualSummary(project: ProjectDetail | null): string {
  if (!project) return "載入中…";
  const parts: string[] = [];
  if (project.watermark_path) {
    const scalePct = Math.round((project.watermark_scale ?? 0.1) * 100);
    parts.push(`浮水印 ✓ ${scalePct}%`);
  } else {
    parts.push("浮水印 — 未上傳");
  }
  const sizeLabel: Record<SubtitleSize, string> = {
    small: "小",
    medium: "中",
    large: "大",
  };
  const posLabel: Record<SubtitlePosition, string> = {
    top: "上",
    middle: "中",
    bottom: "下",
  };
  parts.push(
    `字幕 ${sizeLabel[project.subtitle_size]}${posLabel[project.subtitle_position]}方`,
  );
  return parts.join(" · ");
}

function bgmFilenameFromPath(path: string | null | undefined): string | null {
  if (!path) return null;
  const sep = path.lastIndexOf("/");
  return sep >= 0 ? path.slice(sep + 1) : path;
}

interface SettingsGroupProps {
  title: string;
  summary: string;
  children: React.ReactNode;
}

function SettingsGroup({ title, summary, children }: SettingsGroupProps) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <section
      className={
        "settings-group" + (collapsed ? " settings-group--collapsed" : "")
      }
    >
      <button
        type="button"
        className="settings-group__head"
        aria-expanded={!collapsed}
        onClick={() => setCollapsed((c) => !c)}
      >
        <span className="settings-group__chevron" aria-hidden>
          {collapsed ? "▸" : "▾"}
        </span>
        <span className="settings-group__title">{title}</span>
        <span className="settings-group__summary mono">{summary}</span>
      </button>
      {!collapsed && <div className="settings-group__body">{children}</div>}
    </section>
  );
}

interface EditSettingsBlockProps {
  durationSec: number;
  setDurationSec: (n: number) => void;
  stylePreset: ClipStylePreset;
  setStylePreset: (v: ClipStylePreset) => void;
  stabilize: boolean;
  setStabilize: (v: boolean) => void;
  subtitlesOn: boolean;
  setSubtitlesOn: (v: boolean) => void;
  transitionsOn: boolean;
  setTransitionsOn: (v: boolean) => void;
  autoReframe: boolean;
  setAutoReframe: (v: boolean) => void;
  triggering: boolean;
  validProjectId: number;
  project: ProjectDetail | null;
  setProject: (p: ProjectDetail) => void;
  currentBgmSource: BgmSource;
  setCurrentBgmSource: (s: BgmSource) => void;
}

// v0.20.2 — single source of the settings UI, used by both the
// "尚未產生剪輯" initial card and the "已完成" review card so the
// two states render identically aside from the surrounding actions.
function EditSettingsBlock(props: EditSettingsBlockProps) {
  const bgmFilename = bgmFilenameFromPath(props.project?.bgm_path);
  const basicSummary = formatBasicSummary({
    durationSec: props.durationSec,
    stylePreset: props.stylePreset,
    stabilize: props.stabilize,
    subtitlesOn: props.subtitlesOn,
    transitionsOn: props.transitionsOn,
    autoReframe: props.autoReframe,
  });
  const bgmSummary = formatBgmSummary({
    source: props.currentBgmSource,
    bgmFilename,
  });
  const visualSummary = formatVisualSummary(props.project);

  return (
    <div className="edit-settings">
      <SettingsGroup title="基本剪輯設定" summary={basicSummary}>
        <DurationPicker
          value={props.durationSec}
          onChange={props.setDurationSec}
          disabled={props.triggering}
        />
        <StylePresetPicker
          value={props.stylePreset}
          onChange={props.setStylePreset}
          disabled={props.triggering}
        />
        <RenderOptions
          stabilize={props.stabilize}
          setStabilize={props.setStabilize}
          subtitlesOn={props.subtitlesOn}
          setSubtitlesOn={props.setSubtitlesOn}
          transitionsOn={props.transitionsOn}
          setTransitionsOn={props.setTransitionsOn}
          autoReframe={props.autoReframe}
          setAutoReframe={props.setAutoReframe}
          disabled={props.triggering}
        />
      </SettingsGroup>

      <SettingsGroup title="配樂" summary={bgmSummary}>
        <BgmSourcePicker
          projectId={props.validProjectId}
          bgmPath={props.project?.bgm_path}
          onProjectUpdated={props.setProject}
          disabled={props.triggering}
          stylePreset={props.stylePreset}
          onSourceChange={props.setCurrentBgmSource}
        />
      </SettingsGroup>

      <SettingsGroup title="視覺疊加" summary={visualSummary}>
        <WatermarkPicker
          projectId={props.validProjectId}
          project={props.project}
          onProjectUpdated={props.setProject}
          disabled={props.triggering}
        />
        <SubtitleStyleEditor
          project={props.project}
          onProjectUpdated={props.setProject}
          disabled={props.triggering || !props.subtitlesOn}
        />
      </SettingsGroup>
    </div>
  );
}

interface ProgressTrackerProps {
  steps: Record<string, string> | null | undefined;
}

// Per-stage notes shown when a stage is *running* so the user knows the
// expected duration and stops thinking the worker is stuck.
const RUNNING_STAGE_HINTS: Record<string, string> = {
  plan: "Gemini 為每段素材打分中（約 30–60 秒）。",
  cut: "FFmpeg 把每段素材切片並轉成 9:16（約 30–60 秒）。",
  stabilize:
    "兩階段 vidstab 數位防抖中，每段都跑 detect + transform 兩次，整體約需 2–3 分鐘。這是預期的；沒有卡住。",
  concat: "用 xfade 把每段拼接成完整影片（約 30 秒）。",
  subtitles: "把字幕燒進影片（約 20 秒）。",
  bgm: "與背景音樂混音；沒有 BGM 時直接通過。",
};

function ProgressTracker({ steps }: ProgressTrackerProps) {
  // Find the currently-running stage so we can surface its hint below
  // the chip row. ``EDIT_STEP_ORDER`` walks plan → bgm so the first
  // running stage is the one the user is actually waiting on.
  const runningStage = EDIT_STEP_ORDER.find(
    (step) => steps?.[step] === "running",
  );
  return (
    <div className="edit-progress-wrap">
      <div className="edit-progress" role="list" aria-label="剪輯進度">
        {EDIT_STEP_ORDER.map((step) => {
          const raw = steps?.[step];
          const cls = classifyStepState(raw);
          return (
            <div
              key={step}
              className={`edit-progress__step edit-progress__step--${cls}`}
              role="listitem"
              title={raw ?? "pending"}
            >
              <span className="edit-progress__step-name">
                {EDIT_STEP_LABELS[step]}
              </span>
              <span className="edit-progress__step-state">
                {labelForStepState(raw)}
              </span>
            </div>
          );
        })}
      </div>
      {runningStage && RUNNING_STAGE_HINTS[runningStage] && (
        <p className="edit-progress__hint" aria-live="polite">
          {RUNNING_STAGE_HINTS[runningStage]}
        </p>
      )}
    </div>
  );
}

export default function ProjectEdit() {
  const { id } = useParams<{ id: string }>();
  const projectId = id ? Number(id) : NaN;
  const validProjectId = Number.isFinite(projectId) ? projectId : 0;

  // Full list of drafts for this project. Sorted version-desc so [0] is the
  // newest version. Drives both the version switcher and the polling
  // subscription.
  const [drafts, setDrafts] = useState<DraftSummary[]>([]);
  // Currently displayed version. Defaults to the latest after seed; changes
  // when the user clicks a different chip in <VersionSwitcher>. Never
  // auto-jumps away from a user's manual selection — but does follow when
  // the user just kicked off a new render (handleStartEdit picks the new
  // latest explicitly).
  const [selectedDraftId, setSelectedDraftId] = useState<number | null>(null);
  const [seedLoading, setSeedLoading] = useState<boolean>(true);
  const [seedError, setSeedError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState<boolean>(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<boolean>(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [durationSec, setDurationSec] = useState<number>(DEFAULT_DURATION_S);
  // v0.14.3 — digital stabilization toggle. Default on; user opts out
  // for tripod / gimbal projects to halve render time.
  const [stabilize, setStabilize] = useState<boolean>(true);
  // v0.14.4 — subtitles + transitions toggles. Both default on (matches
  // the API defaults). User can disable to ship a captionless mp4 or
  // hard-cut version without re-rendering the source plan.
  const [subtitlesOn, setSubtitlesOn] = useState<boolean>(true);
  const [transitionsOn, setTransitionsOn] = useState<boolean>(true);
  // v0.16 — auto-reframe (YOLO-tracked dynamic crop). Default on. The
  // backend silently falls back to the static centered crop for assets
  // without tracking_json, so leaving this on is safe even on a half-
  // analyzed project.
  const [autoReframe, setAutoReframe] = useState<boolean>(true);
  // v0.18 — clip-style preset (fast / slow / commercial / artistic /
  // custom). ``custom`` is the legacy free-form default.
  const [stylePreset, setStylePreset] = useState<ClipStylePreset>("custom");
  // v0.20.2 — observed source from <BgmSourcePicker>. Lets the
  // section-title summary line stay in sync without lifting the
  // picker's full state (it has lots of internal AI / library state
  // we don't need up here). Default mirrors the picker's seed:
  // "upload" if a BGM is already on file, "none" otherwise.
  const [currentBgmSource, setCurrentBgmSource] = useState<BgmSource>("none");
  // v0.14.5 — project detail (mostly for bgm_path so the BGM upload
  // button can show "目前：filename.mp3"). Fetched once on mount and
  // refreshed after a successful BGM upload.
  const [project, setProject] = useState<ProjectDetail | null>(null);
  // v0.14.7 — per-asset thumbnail metadata (duration + frame URLs) so
  // each segment card can render the keyframe closest to the cut's
  // mid-point. Fetched once on mount; failure is non-fatal — the cards
  // just render without a thumbnail strip.
  const [assetThumbs, setAssetThumbs] = useState<
    Map<number, { duration_ms: number; thumbnail_urls: string[] }>
  >(new Map());

  const videoRef = useRef<HTMLVideoElement | null>(null);

  const refreshDrafts = useCallback(async (): Promise<DraftSummary[]> => {
    const list = await apiClient.fetchProjectDrafts(validProjectId);
    list.sort((a, b) => b.version - a.version);
    setDrafts(list);
    return list;
  }, [validProjectId]);

  // Initial: pull the full drafts list and seed selection to the newest one.
  useEffect(() => {
    let cancelled = false;
    if (!Number.isFinite(projectId)) return;
    (async () => {
      try {
        const list = await refreshDrafts();
        if (cancelled) return;
        setSelectedDraftId(list[0]?.id ?? null);
        setSeedError(null);
      } catch (err) {
        if (cancelled) return;
        setSeedError(
          err instanceof Error ? err.message : String(err ?? "unknown error"),
        );
      } finally {
        if (!cancelled) setSeedLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, refreshDrafts]);

  // Fetch the project once on mount so the BGM uploader can show the
  // current filename and the toggles know whether bgm exists. Failure
  // is non-fatal — uploader just falls back to the "上傳配樂" label.
  useEffect(() => {
    if (!Number.isFinite(projectId)) return;
    let cancelled = false;
    (async () => {
      try {
        const p = await apiClient.fetchProject(validProjectId);
        if (!cancelled) setProject(p);
      } catch {
        // tolerate
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, validProjectId]);

  // v0.14.7 — pull keyframe galleries for every analysed asset in the
  // project so DraggableTimeline can render a per-cut thumbnail.
  useEffect(() => {
    if (!Number.isFinite(projectId)) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await apiClient.fetchProjectAnalysis(validProjectId);
        if (cancelled) return;
        const map = new Map<
          number,
          { duration_ms: number; thumbnail_urls: string[] }
        >();
        for (const a of data.assets) {
          if (a.thumbnail_urls && a.thumbnail_urls.length > 0) {
            map.set(a.id, {
              duration_ms: a.duration_ms,
              thumbnail_urls: a.thumbnail_urls,
            });
          }
        }
        setAssetThumbs(map);
      } catch {
        // tolerate — just don't show thumbnails
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, validProjectId]);

  const selectedSummary = useMemo(
    () => drafts.find((d) => d.id === selectedDraftId) ?? null,
    [drafts, selectedDraftId],
  );
  const isLatestSelected = drafts.length > 0 && drafts[0].id === selectedDraftId;

  const polling = useDraftPolling(selectedDraftId);
  const draft = polling.data;

  // While the selected version is in flight (pending/processing), poll the
  // drafts list too so the chip status updates live as it transitions to
  // ready_for_review / failed. Cheap — list endpoint is one query.
  useEffect(() => {
    if (!selectedSummary) return;
    const inFlight =
      selectedSummary.status === "pending" ||
      selectedSummary.status === "processing";
    if (!inFlight) return;
    const handle = window.setInterval(() => {
      void refreshDrafts().catch(() => {});
    }, 5_000);
    return () => window.clearInterval(handle);
  }, [selectedSummary, refreshDrafts]);

  const handleStartEdit = useCallback(
    async (force: boolean) => {
      setTriggering(true);
      setTriggerError(null);
      const target = Math.max(
        DURATION_MIN_S,
        Math.min(DURATION_MAX_S, Math.round(durationSec || DEFAULT_DURATION_S)),
      );
      try {
        const resp = await apiClient.triggerProjectEdit(validProjectId, {
          force,
          target_duration_seconds: target,
          stabilize,
          subtitles: subtitlesOn,
          transitions: transitionsOn,
          auto_reframe: autoReframe,
          style_preset: stylePreset,
        });
        // The API now creates the Draft row synchronously, so resp.draft_id
        // is always a real id. Switch the selected version to it immediately
        // — this kicks useDraftPolling into fetching the new row before the
        // list refresh comes back, so the UI never falls back to 開始剪輯.
        setSelectedDraftId(resp.draft_id);
        // Refresh the chips list in the background so the new version
        // shows up. Old versions stay in the list — clicking a chip
        // switches back.
        void refreshDrafts().catch(() => {});
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setTriggerError(
            "已有正在剪輯中的版本；待其完成或勾選「強制重新剪輯」。",
          );
        } else {
          setTriggerError(
            err instanceof Error ? err.message : String(err ?? "unknown error"),
          );
        }
      } finally {
        setTriggering(false);
      }
    },
    [
      validProjectId,
      durationSec,
      stabilize,
      subtitlesOn,
      transitionsOn,
      autoReframe,
      stylePreset,
      refreshDrafts,
    ],
  );

  const handleCancel = useCallback(async () => {
    if (selectedDraftId === null) return;
    if (!window.confirm("確定要停止這次剪輯？已跑的時間會丟掉。")) return;
    setCancelling(true);
    setCancelError(null);
    try {
      await apiClient.cancelDraftRender(selectedDraftId);
      void refreshDrafts().catch(() => {});
    } catch (err) {
      setCancelError(
        err instanceof Error ? err.message : String(err ?? "unknown error"),
      );
    } finally {
      setCancelling(false);
    }
  }, [selectedDraftId, refreshDrafts]);

  const status = draft?.status ?? null;
  // True both for the first-ever trigger (draft is null) and for a force-retry
  // (draft still holds the previous version's data until the next poll lands).
  // Used to suppress stale Failed/Ready cards and to keep 開始剪輯 from
  // reappearing in the brief gap between POST and the first /drafts/{id} fetch.
  const awaitingFirstFetch =
    selectedDraftId !== null && draft?.id !== selectedDraftId;
  const showProcessing =
    (status === "pending" || status === "processing") && !awaitingFirstFetch;
  const showReady = status === "ready_for_review" && !awaitingFirstFetch;
  const showFailed = status === "failed" && !awaitingFirstFetch;
  const showQueued = !draft && !seedLoading && (triggering || awaitingFirstFetch);
  const showInitial =
    !seedLoading && !triggering && !awaitingFirstFetch && drafts.length === 0;

  return (
    <main className="page project-edit">
      <header className="edit-hero">
        <div className="edit-hero__kicker">自動剪輯</div>
        <h1 className="edit-hero__title">
          專案 #{validProjectId}
          {draft && <span className="edit-hero__version mono"> · v{draft.version}</span>}
        </h1>
        <p className="edit-hero__lede mono">
          {draft
            ? labelForDraftStatus(draft.status)
            : seedLoading
              ? "載入中…"
              : triggering || awaitingFirstFetch
                ? "排隊中…"
                : "尚未產生剪輯"}
          {polling.isPolling && draft && (
            <span className="polling-indicator" aria-live="polite">
              {" · 更新中"}
            </span>
          )}
        </p>
        <div className="edit-hero__actions">
          <Link
            to={`/projects/${validProjectId}/assets`}
            className="cta cta--quiet"
          >
            ← 回到分析
          </Link>
          <Link to="/" className="cta cta--quiet">
            專案清單
          </Link>
        </div>
        {(seedError || triggerError || polling.error) && (
          <p className="edit-error" role="alert">
            {seedError || triggerError || polling.error?.message}
          </p>
        )}
      </header>

      <VersionSwitcher
        drafts={drafts}
        selectedId={selectedDraftId}
        onSelect={setSelectedDraftId}
        disabled={triggering}
      />

      {drafts.length > 1 && !isLatestSelected && (
        <p className="edit-hint">
          目前檢視的是舊版 v{selectedSummary?.version ?? "?"}；按「重新剪輯」會建立 v
          {drafts[0].version + 1}，舊版保留。
        </p>
      )}

      {showInitial && (
        <section className="edit-card">
          <h2 className="edit-card__title">準備好就開始</h2>
          <p className="edit-card__body">
            AI 會根據腳本與素材的逐字稿、場景、運鏡，挑選最適合的片段並依節奏拼接成
            一支 9:16 / 4:5 / 1:1 的成品影片，並燒入繁體中文字幕。
          </p>
          <EditSettingsBlock
            durationSec={durationSec}
            setDurationSec={setDurationSec}
            stylePreset={stylePreset}
            setStylePreset={setStylePreset}
            stabilize={stabilize}
            setStabilize={setStabilize}
            subtitlesOn={subtitlesOn}
            setSubtitlesOn={setSubtitlesOn}
            transitionsOn={transitionsOn}
            setTransitionsOn={setTransitionsOn}
            autoReframe={autoReframe}
            setAutoReframe={setAutoReframe}
            triggering={triggering}
            validProjectId={validProjectId}
            project={project}
            setProject={setProject}
            currentBgmSource={currentBgmSource}
            setCurrentBgmSource={setCurrentBgmSource}
          />
          <div className="edit-card__actions">
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleStartEdit(false)}
              disabled={triggering}
            >
              {triggering ? "排隊中…" : `開始剪輯（${durationSec} 秒）`}
            </button>
          </div>
        </section>
      )}

      {showQueued && (
        <section className="edit-card" aria-live="polite">
          <h2 className="edit-card__title">排隊中…</h2>
          <p className="edit-card__body">
            已建立剪輯任務，正在等候 worker 取件。畫面會在 worker 開始處理後自動更新。
          </p>
          <ProgressTracker steps={null} />
        </section>
      )}

      {showProcessing && draft && (
        <section className="edit-card">
          <h2 className="edit-card__title">剪輯中…</h2>
          <ProgressTracker steps={draft.progress_steps} />
          {draft.cut_plan?.notes && (
            <p className="edit-card__hint mono">「{draft.cut_plan.notes}」</p>
          )}
          {draft.cut_plan?.used_fallback && (
            <p className="edit-hint">
              已切換為備用規劃（{draft.cut_plan.fallback_reason || "未知原因"}）。
            </p>
          )}
          <div className="edit-card__actions">
            <button
              type="button"
              className="cta cta--danger"
              onClick={() => void handleCancel()}
              disabled={cancelling}
            >
              {cancelling ? "停止中…" : "停止剪輯"}
            </button>
          </div>
          {cancelError && (
            <p className="edit-error" role="alert">
              停止失敗：{cancelError}
            </p>
          )}
        </section>
      )}

      {showReady && draft && (
        <>
          <section className="edit-preview">
            {draft.mp4_url ? (
              <video
                ref={videoRef}
                className="edit-preview__video"
                src={draft.mp4_url}
                controls
                playsInline
                preload="metadata"
              />
            ) : (
              <div className="edit-preview__placeholder mono">
                檔案尚未就緒，請稍候重試。
              </div>
            )}
          </section>
          <section className="edit-card">
            <div className="edit-card__row">
              <h2 className="edit-card__title">片段順序</h2>
              <div className="edit-card__actions">
                {draft.mp4_url && (
                  <a
                    className="cta cta--primary"
                    href={draft.mp4_url}
                    download={`project-${validProjectId}-v${draft.version}.mp4`}
                  >
                    下載成品
                  </a>
                )}
                {draft.subtitle_url && (
                  <a className="cta cta--quiet" href={draft.subtitle_url} download>
                    下載字幕
                  </a>
                )}
                <button
                  type="button"
                  className="cta"
                  onClick={() => void handleStartEdit(true)}
                  disabled={triggering}
                >
                  {triggering ? "排隊中…" : `重新剪輯（${durationSec} 秒）`}
                </button>
              </div>
            </div>
            <EditSettingsBlock
              durationSec={durationSec}
              setDurationSec={setDurationSec}
              stylePreset={stylePreset}
              setStylePreset={setStylePreset}
              stabilize={stabilize}
              setStabilize={setStabilize}
              subtitlesOn={subtitlesOn}
              setSubtitlesOn={setSubtitlesOn}
              transitionsOn={transitionsOn}
              setTransitionsOn={setTransitionsOn}
              autoReframe={autoReframe}
              setAutoReframe={setAutoReframe}
              triggering={triggering}
              validProjectId={validProjectId}
              project={project}
              setProject={setProject}
              currentBgmSource={currentBgmSource}
              setCurrentBgmSource={setCurrentBgmSource}
            />
            <div className="edit-card__advanced-row">
              <Link
                to={`/projects/${validProjectId}/edit/timeline/${draft.id}`}
                className="cta cta--secondary edit-card__advanced-link"
              >
                進階編輯 ✨
              </Link>
              <span className="edit-card__advanced-hint">
                打開時間軸視圖，可裁切、分割、刪除片段
              </span>
            </div>
            <DraggableTimeline
              draft={draft}
              videoRef={videoRef as React.RefObject<HTMLVideoElement>}
              assetThumbs={assetThumbs}
              onReorderStart={() => void refreshDrafts().catch(() => {})}
              onReorderCommitted={(fresh) => {
                // The PATCH already returned the fresh DraftDetail
                // (status=processing, reset progress_steps_json). Pump
                // it into the polling hook so the UI flips from 已完成
                // → 剪輯中 immediately. Also nudge the drafts list so
                // the version chip mirrors the new state.
                polling.applyDraft(fresh);
                void refreshDrafts().catch(() => {});
              }}
              onReorderError={(msg) => setTriggerError(msg)}
            />
            {draft.cut_plan?.notes && (
              <p className="edit-card__hint mono">「{draft.cut_plan.notes}」</p>
            )}
            {draft.cut_plan?.used_fallback && (
              <p className="edit-hint">
                已切換為備用規劃（{draft.cut_plan.fallback_reason || "未知原因"}）。
              </p>
            )}
            <ExportSheet
              draftId={draft.id}
              draftVersion={draft.version}
              ready
            />
          </section>
          <SubtitleEditor
            draftId={draft.id}
            locked={triggering || awaitingFirstFetch || showProcessing}
            onRebuildStart={() => void refreshDrafts().catch(() => {})}
            onRebuildError={(msg) => setTriggerError(msg)}
          />
        </>
      )}

      {showFailed && draft && (
        <section className="edit-card edit-card--failed">
          <h2 className="edit-card__title">剪輯失敗</h2>
          <ProgressTracker steps={draft.progress_steps} />
          {draft.prompt_feedback && (
            <pre className="edit-card__error mono">{draft.prompt_feedback}</pre>
          )}
          <div className="edit-card__actions">
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleStartEdit(true)}
              disabled={triggering}
            >
              {triggering ? "排隊中…" : "重新剪輯"}
            </button>
          </div>
        </section>
      )}

      {selectedDraftId !== null && <DraftComments draftId={selectedDraftId} />}
    </main>
  );
}
