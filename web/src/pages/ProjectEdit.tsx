import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, apiClient } from "../api/client";
import BgmFadeOutSlider from "../components/BgmFadeOutSlider";
import BgmSourcePicker from "../components/BgmSourcePicker";
import type { BgmSource } from "../components/BgmSourcePicker";
import CropRegionPicker, {
  type CropDirection,
} from "../components/CropRegionPicker";
import DraggableTimeline from "../components/DraggableTimeline";
import ExportSheet from "../components/ExportSheet";
import QueueStatusModal from "../components/QueueStatusModal";
import SubtitleEditor from "../components/SubtitleEditor";
import SubjectClassPicker from "../components/SubjectClassPicker";
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

const ANALYSIS_STEP_ORDER = [
  "stt",
  "scene",
  "motion",
  "emotion",
  "tracking",
  "coverage",
] as const;

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
          placeholder="告訴系統下次怎麼改進這個版本（例：「不要轉場特效」「蚊子館重複太多」「片頭再有力一點」）。下次重新產生時，這裡的留言會作為改進指引。"
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
    <nav className="version-switcher" aria-label="短影音版本">
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
    <div className="duration-picker" aria-label="影片總長度">
      <span className="duration-picker__label">影片總長度</span>
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
  transitionHint: string;
  bgmHint: string;
}

// v0.21.5 — dropped the technical "片段 X-Y 秒" line. Operators see
// one finished video; per-cut span bounds are an internal planner
// detail, not something they tune. The card now reads as a one-line
// "what this style sounds + transitions like" pair.
const STYLE_PRESET_CARDS: readonly StylePresetCard[] = [
  {
    value: "fast",
    label: "快節奏",
    icon: "⚡",
    transitionHint: "畫面切換俐落，節奏密集",
    bgmHint: "高能量、適合促銷或活動",
  },
  {
    value: "slow",
    label: "慢節奏",
    icon: "🌊",
    transitionHint: "畫面柔和銜接，留白較多",
    bgmHint: "溫暖、適合生活感內容",
  },
  {
    value: "commercial",
    label: "商業感",
    icon: "🏷️",
    transitionHint: "重點清楚，節奏穩定",
    bgmHint: "乾淨、有品牌感",
  },
  {
    value: "artistic",
    label: "文青風",
    icon: "🎨",
    transitionHint: "氛圍感較強，轉換較慢",
    bgmHint: "輕柔、適合故事感內容",
  },
  {
    value: "custom",
    label: "自訂",
    icon: "✦",
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
      <legend className="style-preset-picker__legend">短影音風格</legend>
      <p className="style-preset-picker__hint mono">
        選一種成品感覺，系統會自動調整節奏與配樂方向。
      </p>
      <div
        className="style-preset-picker__grid"
        role="radiogroup"
        aria-label="短影音風格"
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
                節奏：{card.transitionHint}
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
  smartCamera: boolean;
  setSmartCamera: (v: boolean) => void;
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
  smartCamera,
  setSmartCamera,
  disabled,
}: RenderOptionsProps) {
  return (
    <div className="render-options">
      <EditOptionToggle
        label="畫面防手震"
        hint="手機手持拍攝建議開啟；畫面本來很穩時可關閉，加快成品產出。"
        value={stabilize}
        onChange={setStabilize}
        disabled={disabled}
      />
      <EditOptionToggle
        label="加上字幕"
        hint="開啟後會把繁體中文字幕放進影片，適合社群靜音觀看。"
        value={subtitlesOn}
        onChange={setSubtitlesOn}
        disabled={disabled}
      />
      <EditOptionToggle
        label="使用轉場效果"
        hint="開啟後片段之間會更柔順；關閉則節奏更直接。"
        value={transitionsOn}
        onChange={setTransitionsOn}
        disabled={disabled}
      />
      <EditOptionToggle
        label="自動跟住主角"
        hint="建立直式或方形影片時，系統會盡量讓人物、車或商品留在畫面中間。"
        value={autoReframe}
        onChange={setAutoReframe}
        disabled={disabled}
      />
      <EditOptionToggle
        label="AI 智慧運鏡（實驗性）"
        hint="啟用後重新產生時會多打一次 Gemini 規劃鏡頭運動。可能蓋過情緒縮放；與穩定畫面、跟住主角同時開啟時會自動退讓。"
        value={smartCamera}
        onChange={setSmartCamera}
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
  if (opts.stabilize) flags.push("畫面更穩");
  const flagText = flags.length > 0 ? flags.join(" / ") : "直接銜接";
  return `${opts.durationSec} 秒 · ${STYLE_PRESET_LABELS[opts.stylePreset]} · ${flagText}`;
}

function formatBgmSummary(opts: {
  source: BgmSource;
  bgmFilename: string | null;
}): string {
  switch (opts.source) {
    case "none":
      return "不使用配樂";
    case "preset":
      return opts.bgmFilename
        ? `風格預設配樂：${opts.bgmFilename}`
        : "依風格自動配樂（待產生）";
    case "library":
      return opts.bgmFilename
        ? `音樂庫：${opts.bgmFilename}`
        : "從音樂庫選擇（待挑選）";
    case "ai":
      return opts.bgmFilename
        ? `自訂配樂：${opts.bgmFilename}`
        : "自訂配樂（待產生）";
    case "upload":
      return opts.bgmFilename
        ? `已上傳：${opts.bgmFilename}`
        : "自行上傳（待選檔）";
  }
}

function formatVisualSummary(project: ProjectDetail | null): string {
  if (!project) return "載入中…";
  const parts: string[] = [];
  if (project.subject_class) {
    parts.push(`主角 🎯 ${project.subject_class}`);
  }
  if (project.watermark_path) {
    const scalePct = Math.round((project.watermark_scale ?? 0.1) * 100);
    parts.push(`品牌標誌 ✓ ${scalePct}%`);
  } else {
    parts.push("品牌標誌 — 未上傳");
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
  smartCamera: boolean;
  setSmartCamera: (v: boolean) => void;
  triggering: boolean;
  validProjectId: number;
  project: ProjectDetail | null;
  setProject: (p: ProjectDetail) => void;
  currentBgmSource: BgmSource;
  setCurrentBgmSource: (s: BgmSource) => void;
  // v0.29.0 — null when source orientation matches target (no crop
  // needed); otherwise the axis being cropped. Drives whether
  // CropRegionPicker mounts.
  cropDirection: CropDirection | null;
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
      <SettingsGroup title="短影音設定" summary={basicSummary}>
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
          smartCamera={props.smartCamera}
          setSmartCamera={props.setSmartCamera}
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
        <BgmFadeOutSlider
          project={props.project}
          onProjectUpdated={props.setProject}
          disabled={props.triggering}
        />
      </SettingsGroup>

      <SettingsGroup title="畫面與品牌" summary={visualSummary}>
        <SubjectClassPicker
          project={props.project}
          onProjectUpdated={props.setProject}
          disabled={props.triggering}
        />
        {props.cropDirection !== null && (
          <CropRegionPicker
            project={props.project}
            direction={props.cropDirection}
            onProjectUpdated={props.setProject}
            disabled={props.triggering}
          />
        )}
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
  plan: "正在挑出最適合社群觀看的精彩片段，通常需要 30–60 秒。",
  cut: "正在整理素材並做成短影音尺寸，通常需要 30–60 秒。",
  stabilize: "正在讓手持畫面更穩，影片較長時可能需要 2–3 分鐘。",
  concat: "正在把片段接成一支完整影片，通常約 30 秒。",
  subtitles: "正在加上繁體中文字幕，方便社群靜音觀看。",
  bgm: "正在整理背景音樂與原聲音量。",
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
      <div className="edit-progress" role="list" aria-label="成品進度">
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
  // v0.25.0 — queue inspector modal. Opened from the "等待開始" card's
  // "查看處理狀態" button so the operator can see what's blocking and
  // (optionally) drop their own pending job.
  const [queueModalOpen, setQueueModalOpen] = useState<boolean>(false);
  const [durationSec, setDurationSec] = useState<number>(DEFAULT_DURATION_S);
  // v0.14.3 — digital stabilization toggle. Default on; user opts out
  // for tripod / gimbal projects to halve render time.
  const [stabilize, setStabilize] = useState<boolean>(true);
  // v0.14.4 — subtitles + transitions toggles. ``subtitles`` defaults
  // on. ``transitions`` defaults OFF as of v0.24.0 — every operator
  // who tested fresh projects switched transitions off as their
  // first action; the default now matches that workflow. Slow /
  // artistic / commercial style presets that benefit from xfade can
  // still re-enable the toggle on the trigger panel.
  const [subtitlesOn, setSubtitlesOn] = useState<boolean>(true);
  const [transitionsOn, setTransitionsOn] = useState<boolean>(false);
  // v0.16 — auto-reframe (YOLO-tracked dynamic crop). Default on. The
  // backend silently falls back to the static centered crop for assets
  // without tracking_json, so leaving this on is safe even on a half-
  // analyzed project.
  const [autoReframe, setAutoReframe] = useState<boolean>(true);
  // v0.30.0 — opt-in AI Smart Camera. Default off; we sync to the
  // persistent ``Project.smart_camera_enabled`` once the project
  // detail loads (see effect below). Toggling the checkbox PATCHes
  // the project so the value persists across page reloads.
  const [smartCamera, setSmartCamera] = useState<boolean>(false);
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
  // v0.21.6 — analysis status gate: the trigger buttons are disabled
  // while any per-asset analysis step is still running / pending so
  // the operator doesn't kick off a render against half-analysed
  // material. ``null`` until the first fetch resolves so we can show
  // an "analysis loading" hint rather than enabling buttons too early.
  // ``inFlight`` is the count of (asset × step) pairs still working;
  // failed steps are treated as terminal (don't block) since they
  // need manual retry on the analysis page anyway.
  const [analysisStatus, setAnalysisStatus] = useState<{
    allDone: boolean;
    inFlight: number;
    failed: number;
    total: number;
  } | null>(null);
  // v0.21.6 — completion toast. Set to a string when analysis flips
  // from incomplete → done while the user is on this page; a
  // useEffect timer clears it after ~4 seconds. ``transitionRef``
  // tracks the previous allDone value so we only fire the toast on
  // the false → true edge (not on every re-fetch that returns done).
  const [analysisToast, setAnalysisToast] = useState<string | null>(null);
  const prevAnalysisAllDoneRef = useRef<boolean | null>(null);
  // v0.14.7 — per-asset thumbnail metadata (duration + frame URLs) so
  // each segment card can render the keyframe closest to the cut's
  // mid-point. Fetched once on mount; failure is non-fatal — the cards
  // just render without a thumbnail strip.
  const [assetThumbs, setAssetThumbs] = useState<
    Map<number, { duration_ms: number; thumbnail_urls: string[] }>
  >(new Map());
  // v0.29.0 — aggregate source-asset orientation. ``"portrait"`` =
  // every analysed asset has h > w; ``"landscape"`` = every asset
  // has w >= h; ``"mixed"`` = both kinds present (rare; we still
  // surface the picker because the static crop has to pick a
  // direction); ``null`` = nothing analysed yet (picker stays
  // hidden — operator can re-open the page after analysis lands).
  const [sourceOrientation, setSourceOrientation] = useState<
    "portrait" | "landscape" | "mixed" | null
  >(null);

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
        if (!cancelled) {
          setProject(p);
          // v0.30.0 — seed the AI Smart Camera checkbox from the
          // persistent project toggle. Renders unchecked by default
          // for legacy projects whose backend pre-dates the column.
          setSmartCamera(Boolean(p.smart_camera_enabled));
        }
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
  // v0.21.6 — also computes the per-step analysis status so the
  // trigger button can disable itself while any step is still running
  // / pending. Polls every 5 s while incomplete; stops once allDone.
  useEffect(() => {
    if (!Number.isFinite(projectId)) return;
    let cancelled = false;
    let timer: number | null = null;

    async function tick() {
      try {
        const data = await apiClient.fetchProjectAnalysis(validProjectId);
        if (cancelled) return;
        const map = new Map<
          number,
          { duration_ms: number; thumbnail_urls: string[] }
        >();
        let inFlight = 0;
        let failed = 0;
        let total = 0;
        let portraitCount = 0;
        let landscapeCount = 0;
        for (const a of data.assets) {
          if (a.thumbnail_urls && a.thumbnail_urls.length > 0) {
            map.set(a.id, {
              duration_ms: a.duration_ms,
              thumbnail_urls: a.thumbnail_urls,
            });
          }
          // v0.29.0 — parse "1080x1920" / "1920x1080" into orientation
          // counts so the CropRegionPicker only mounts when source ≠
          // target orientation. Tolerant: missing / malformed values
          // (e.g. ``null`` resolution after ffprobe failure) are
          // ignored rather than skewing the count.
          const res = a.resolution;
          if (typeof res === "string") {
            const match = res.match(/^(\d+)x(\d+)$/i);
            if (match) {
              const w = Number(match[1]);
              const h = Number(match[2]);
              if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
                if (h > w) portraitCount += 1;
                else landscapeCount += 1;
              }
            }
          }
          const steps = a.analysis_steps ?? {};
          for (const step of ANALYSIS_STEP_ORDER) {
            const state = steps[step];
            total += 1;
            if (!state || state === "running" || state === "pending") inFlight += 1;
            else if (typeof state === "string" && state.startsWith("failed:"))
              failed += 1;
          }
        }
        setAssetThumbs(map);
        if (portraitCount === 0 && landscapeCount === 0) {
          setSourceOrientation(null);
        } else if (portraitCount > 0 && landscapeCount === 0) {
          setSourceOrientation("portrait");
        } else if (landscapeCount > 0 && portraitCount === 0) {
          setSourceOrientation("landscape");
        } else {
          setSourceOrientation("mixed");
        }
        const next = {
          allDone: data.assets.length > 0 && total > 0 && inFlight === 0,
          inFlight,
          failed,
          total,
        };
        setAnalysisStatus(next);
        // Edge-trigger the completion toast when allDone goes false → true.
        const prev = prevAnalysisAllDoneRef.current;
        if (next.allDone && prev === false) {
          setAnalysisToast("素材檢查完成！");
        }
        prevAnalysisAllDoneRef.current = next.allDone;
        // Schedule the next poll only if there's still work to wait on.
        if (!cancelled && !next.allDone) {
          timer = window.setTimeout(() => void tick(), 5_000);
        }
      } catch {
        // tolerate — just don't show thumbnails / status. Try again
        // on the next poll cycle so a transient API hiccup doesn't
        // permanently leave the trigger button disabled.
        if (!cancelled) timer = window.setTimeout(() => void tick(), 5_000);
      }
    }

    void tick();
    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [projectId, validProjectId]);

  // v0.21.6 — auto-dismiss the completion toast after 4 s.
  useEffect(() => {
    if (analysisToast === null) return;
    const handle = window.setTimeout(() => setAnalysisToast(null), 4_000);
    return () => window.clearTimeout(handle);
  }, [analysisToast]);

  const selectedSummary = useMemo(
    () => drafts.find((d) => d.id === selectedDraftId) ?? null,
    [drafts, selectedDraftId],
  );
  const isLatestSelected = drafts.length > 0 && drafts[0].id === selectedDraftId;

  // v0.29.0 — show CropRegionPicker only when at least one analysed
  // asset has an orientation that disagrees with the project's
  // target_aspect_ratio. Same-orientation projects don't need a
  // static crop anchor — the renderer's centre crop is correct by
  // construction.
  const cropDirection: CropDirection | null = useMemo(() => {
    if (!project || sourceOrientation === null) return null;
    const target = project.target_aspect_ratio;
    if (target === "9:16") {
      // Target is portrait. Picker only matters for landscape
      // (or mixed — pick the offending direction; the user gets
      // left/center/right which controls cropping the wide axis).
      if (sourceOrientation === "landscape") return "horizontal";
      if (sourceOrientation === "mixed") return "horizontal";
      return null;
    }
    if (target === "16:9") {
      if (sourceOrientation === "portrait") return "vertical";
      if (sourceOrientation === "mixed") return "vertical";
      return null;
    }
    return null;
  }, [project, sourceOrientation]);

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

  // v0.30.0 — wrap setSmartCamera so flipping the experimental
  // checkbox also PATCHes the persistent ``Project.smart_camera_enabled``
  // toggle. We update the local state optimistically so the
  // checkbox doesn't lag, and revert + surface a console error if
  // the PATCH fails (the operator can re-toggle to retry).
  const handleSmartCameraChange = useCallback(
    (next: boolean) => {
      setSmartCamera(next);
      void apiClient
        .patchProjectSmartCamera(validProjectId, { enabled: next })
        .then((updated) => setProject(updated))
        .catch((err) => {
          // Roll back the local checkbox so the UI doesn't lie about
          // what the backend has — and surface so the operator
          // notices the failure rather than silently re-trying on
          // the next render.
          setSmartCamera(!next);
          // eslint-disable-next-line no-console
          console.warn("patchProjectSmartCamera failed", err);
        });
    },
    [validProjectId],
  );

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
          smart_camera: smartCamera,
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
            "已有正在製作中的版本；待其完成後再重新產生。",
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
      smartCamera,
      stylePreset,
      refreshDrafts,
    ],
  );

  // v0.22.1 — re-render the currently selected draft against the
  // current project settings without letting the AI re-shuffle
  // segments. Maps to POST /drafts/{id}/re-render. Operator's
  // toggle state on this page wins via render_flags override (same
  // priority as reorder + rebuild-subtitles).
  const handleReRender = useCallback(async () => {
    if (selectedDraftId === null) return;
    setTriggering(true);
    setTriggerError(null);
    try {
      const fresh = await apiClient.reRenderDraft(selectedDraftId, {
        render_flags: {
          transitions: transitionsOn,
          stabilize,
          subtitles: subtitlesOn,
          auto_reframe: autoReframe,
          smart_camera: smartCamera,
        },
      });
      // The endpoint resets status to pending + reset progress and
      // synchronously enqueues the worker. Push the fresh detail
      // into the polling hook so the status pill flips to 剪輯中
      // immediately, and refresh the draft list so the chip mirrors.
      polling.applyDraft(fresh);
      void refreshDrafts().catch(() => {});
    } catch (err) {
      setTriggerError(
        err instanceof Error ? err.message : String(err ?? "unknown error"),
      );
    } finally {
      setTriggering(false);
    }
  }, [
    selectedDraftId,
    transitionsOn,
    stabilize,
    subtitlesOn,
    autoReframe,
    smartCamera,
    polling,
    refreshDrafts,
  ]);

  const handleCancel = useCallback(async () => {
    if (selectedDraftId === null) return;
    if (!window.confirm("確定要停止這次產生？已處理的進度會丟掉。")) return;
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
  const analysisBlocked = analysisStatus !== null && !analysisStatus.allDone;
  const publishingChecklist = useMemo(() => {
    if (!draft) return [];
    return [
      {
        label: "主成品",
        value: draft.mp4_url ? "可預覽與下載" : "檔案整理中",
      },
      {
        label: "社群尺寸",
        value: draft.cut_plan?.target_aspect_ratio
          ?? project?.target_aspect_ratio
          ?? "依專案設定",
      },
      {
        label: "字幕",
        value: draft.subtitle_url
          ? "已加上，字幕檔也可下載"
          : "沒有字幕檔，請預覽確認",
      },
      {
        label: "聲音",
        value: "請預覽確認原聲與配樂",
      },
      {
        label: "品牌標示",
        value: "請預覽確認品牌標誌",
      },
    ];
  }, [draft, project]);

  return (
    <main className="page project-edit">
      <header className="edit-hero">
        <div className="edit-hero__kicker">短影音成品</div>
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
                ? "送出中…"
                : "尚未產生短影音"}
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
            ← 回到素材檢查
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
          目前檢視的是舊版 v{selectedSummary?.version ?? "?"}；按「重新產生」會建立 v
          {drafts[0].version + 1}，舊版保留。
        </p>
      )}

      {/* v0.21.6 — analysis-status banner. Shown across every state
         (initial / queued / processing / ready) so the operator sees
         the warning regardless of whether they're about to trigger
         a fresh edit or already viewing a finished version. */}
      {analysisStatus !== null && !analysisStatus.allDone ? (
        <div
          className="analysis-banner analysis-banner--running"
          role="status"
          aria-live="polite"
        >
          <span className="analysis-banner__icon" aria-hidden>
            ⏳
          </span>
          <div className="analysis-banner__body">
            <strong>素材檢查尚未完成（剩 {analysisStatus.inFlight} 項）</strong>
            <span className="analysis-banner__hint">
              現在產生成品可能不完整 — 等素材檢查完成後再開始，成品會更穩。
              {analysisStatus.failed > 0 ? (
                <>
                  {" "}有 {analysisStatus.failed} 項檢查失敗；到「素材檢查」頁可手動重試。
                </>
              ) : null}
            </span>
          </div>
        </div>
      ) : null}

      {/* v0.21.6 — completion toast. Top-right floating chip,
         auto-dismisses 4 s after analysis allDone goes false → true.
         Uses ``key`` so React re-mounts the node and the CSS
         animation replays even if the message is set twice in
         quick succession. */}
      {analysisToast !== null ? (
        <div
          key={analysisToast}
          className="analysis-toast"
          role="status"
          aria-live="polite"
        >
          <span aria-hidden>✓</span>
          <span>{analysisToast}</span>
        </div>
      ) : null}

      {showInitial && (
        <section className="edit-card">
          <h2 className="edit-card__title">準備好就產生短影音</h2>
          <p className="edit-card__body">
            系統會依照腳本與影片內容，挑出適合社群觀看的片段，做成可發佈的
            IG / FB 短影音，並加上繁體中文字幕。
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
            smartCamera={smartCamera}
            setSmartCamera={handleSmartCameraChange}
            triggering={triggering}
            validProjectId={validProjectId}
            project={project}
            setProject={setProject}
            currentBgmSource={currentBgmSource}
            setCurrentBgmSource={setCurrentBgmSource}
            cropDirection={cropDirection}
          />
          <div className="edit-card__actions">
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleStartEdit(false)}
              disabled={
                triggering
                || analysisBlocked
              }
              title={
                analysisBlocked
                  ? "等待素材檢查完成後即可產生成品"
                  : undefined
              }
            >
              {triggering
                ? "等待開始…"
                : analysisBlocked
                  ? `素材檢查中（剩 ${analysisStatus?.inFlight ?? 0} 項），完成後可開始`
                  : `產生 ${durationSec} 秒短影音`}
            </button>
          </div>
        </section>
      )}

      {showQueued && (
        <section className="edit-card" aria-live="polite">
          <h2 className="edit-card__title">等待開始…</h2>
          <p className="edit-card__body">
            已送出短影音處理項目，正在等待開始。開始後畫面會自動更新。
          </p>
          <div className="edit-card__actions">
            <button
              type="button"
              className="cta cta--secondary"
              onClick={() => setQueueModalOpen(true)}
            >
              查看處理狀態
            </button>
          </div>
          <ProgressTracker steps={null} />
        </section>
      )}

      {showProcessing && draft && (
        <section className="edit-card">
          <h2 className="edit-card__title">正在產生成品…</h2>
          <ProgressTracker steps={draft.progress_steps} />
          {draft.cut_plan?.notes && (
            <p className="edit-card__hint mono">「{draft.cut_plan.notes}」</p>
          )}
          {draft.cut_plan?.used_fallback && (
            <p className="edit-hint">
              已用保守方式產生成品（{draft.cut_plan.fallback_reason || "原因未明"}）。
            </p>
          )}
          <div className="edit-card__actions">
            <button
              type="button"
              className="cta cta--danger"
              onClick={() => void handleCancel()}
              disabled={cancelling}
            >
              {cancelling ? "停止中…" : "停止產生"}
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

          <section className="publish-workbench" aria-label="成品發佈工作台">
            <div className="publish-workbench__intro">
              <p className="publish-workbench__eyebrow mono">發布工作台</p>
              <h2 className="publish-workbench__title">可以直接拿去發 IG / FB</h2>
              <p className="publish-workbench__body">
                先預覽主成品；沒問題就下載，或一鍵建立 Reels、貼文牆、方形貼文版本。
              </p>
            </div>

            <dl className="publish-checklist">
              {publishingChecklist.map((item) => (
                <div key={item.label} className="publish-checklist__item">
                  <dt>{item.label}</dt>
                  <dd>{item.value}</dd>
                </div>
              ))}
            </dl>

            <div className="publish-workbench__actions">
              <button
                type="button"
                className="cta cta--primary"
                onClick={() => videoRef.current?.scrollIntoView({
                  behavior: "smooth",
                  block: "center",
                })}
                disabled={!draft.mp4_url}
              >
                預覽成品
              </button>
              {draft.mp4_url && (
                <a
                  className="cta cta--primary"
                  href={draft.mp4_url}
                  download={`project-${validProjectId}-v${draft.version}.mp4`}
                >
                  下載主成品
                </a>
              )}
              {draft.subtitle_url && (
                <a className="cta cta--quiet" href={draft.subtitle_url} download>
                  下載字幕檔
                </a>
              )}
              <ExportSheet
                draftId={draft.id}
                draftVersion={draft.version}
                ready
              />
              <button
                type="button"
                className="cta cta--quiet"
                onClick={() => void handleStartEdit(true)}
                disabled={triggering || analysisBlocked}
                title={
                  analysisBlocked
                    ? "等待素材檢查完成後即可重新產生"
                    : "重新挑選片段，建立另一個版本"
                }
              >
                {triggering
                  ? "送出中…"
                  : analysisBlocked
                    ? `素材檢查中（剩 ${analysisStatus?.inFlight ?? 0} 項）`
                    : "重新產生一版"}
              </button>
            </div>
          </section>

          <details className="edit-advanced-panel">
            <summary className="edit-advanced-panel__summary">
              <span className="edit-advanced-panel__title">進階微調</span>
              <span className="edit-advanced-panel__hint">
                需要改片段、字幕、配樂或品牌標誌時再打開。
              </span>
            </summary>

            <section className="edit-card edit-card--secondary">
              <div className="edit-card__row">
                <div>
                  <h2 className="edit-card__title">片段與設定微調</h2>
                  <p className="edit-card__body">
                    這裡保留給需要手動調整的人；一般發佈可直接使用上方工作台。
                  </p>
                </div>
                <div className="edit-card__actions">
                  {draft.mp4_url && (
                    <a
                      className="cta cta--quiet"
                      href={draft.mp4_url}
                      download={`project-${validProjectId}-v${draft.version}.mp4`}
                    >
                      下載主成品
                    </a>
                  )}
                  <button
                    type="button"
                    className="cta cta--secondary"
                    onClick={() => void handleReRender()}
                    disabled={triggering}
                    title="保留目前片段順序，只用最新的配樂、字幕、品牌標誌與轉場設定重新產生成品"
                  >
                    {triggering ? "送出中…" : "套用設定再產生"}
                  </button>
                  <button
                    type="button"
                    className="cta"
                    onClick={() => void handleStartEdit(true)}
                    disabled={triggering || analysisBlocked}
                    title={
                      analysisBlocked
                        ? "等待素材檢查完成後即可重新挑選片段"
                        : "重新挑選片段；目前的順序會被覆蓋"
                    }
                  >
                    {triggering
                      ? "送出中…"
                      : analysisBlocked
                        ? `素材檢查中（剩 ${analysisStatus?.inFlight ?? 0} 項）`
                        : `重新選片段（${durationSec} 秒）`}
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
                smartCamera={smartCamera}
                setSmartCamera={handleSmartCameraChange}
                triggering={triggering}
                validProjectId={validProjectId}
                project={project}
                setProject={setProject}
                currentBgmSource={currentBgmSource}
                setCurrentBgmSource={setCurrentBgmSource}
                cropDirection={cropDirection}
              />
              <div className="edit-card__advanced-row">
                <Link
                  to={`/projects/${validProjectId}/edit/timeline/${draft.id}`}
                  className="cta cta--secondary edit-card__advanced-link"
                >
                  進階片段編輯
                </Link>
                <span className="edit-card__advanced-hint">
                  打開時間軸，可調整順序、分割或刪除片段
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
                renderFlags={{
                  transitions: transitionsOn,
                  stabilize,
                  subtitles: subtitlesOn,
                  autoReframe,
                }}
              />
            {draft.cut_plan?.notes && (
              <p className="edit-card__hint mono">「{draft.cut_plan.notes}」</p>
            )}
            {draft.cut_plan?.used_fallback && (
              <p className="edit-hint">
                已用保守方式產生成品（{draft.cut_plan.fallback_reason || "原因未明"}）。
              </p>
            )}
            </section>

            <SubtitleEditor
              draftId={draft.id}
              locked={triggering || awaitingFirstFetch || showProcessing}
              onRebuildStart={() => void refreshDrafts().catch(() => {})}
              onRebuildError={(msg) => setTriggerError(msg)}
              renderFlags={{
                transitions: transitionsOn,
                stabilize,
                subtitles: subtitlesOn,
                autoReframe,
              }}
            />
          </details>
        </>
      )}

      {showFailed && draft && (() => {
        // v0.25.1 — orphan detection (server-side mark in
        // GET /drafts/{id}) sets prompt_feedback to a recognisable
        // string. Surface a friendlier copy + skip the progress
        // bar (which would just show all steps as "等待" and read
        // as a frozen render rather than a missing one).
        const isOrphan = (draft.prompt_feedback || "").startsWith(
          "render: orphaned",
        );
        return (
          <section className="edit-card edit-card--failed">
            <h2 className="edit-card__title">
              {isOrphan ? "這次沒有完成" : "短影音產生失敗"}
            </h2>
            <p className="edit-card__body">
              {isOrphan
                ? "這次產生成品的處理中斷或逾時，沒有成功完成。請點下方按鈕重新送出。"
                : "這次成品沒有成功產出。下方會標出停在哪一步；常見原因是素材不夠、AI 暫時忙碌，或某段影片格式不穩。"}
            </p>
            {!isOrphan && <ProgressTracker steps={draft.progress_steps} />}
            {draft.prompt_feedback && (
              <details className="edit-card__error-details">
                <summary>展開錯誤細節（給開發者參考）</summary>
                <pre className="edit-card__error mono">
                  {draft.prompt_feedback}
                </pre>
              </details>
            )}
            <div className="edit-card__actions">
              <button
                type="button"
                className="cta cta--primary"
                onClick={() => void handleStartEdit(true)}
                disabled={triggering}
              >
                {triggering
                  ? "送出中…"
                  : isOrphan
                    ? "重新送出"
                    : "重新產生一版"}
              </button>
            </div>
          </section>
        );
      })()}

      {selectedDraftId !== null && <DraftComments draftId={selectedDraftId} />}

      {/* v0.25.0 — queue inspector. Mounted at page level so the
          "等待開始" card's "查看處理狀態" button can pop it without nested
          DOM constraints. ``highlightDraftId`` so the user's own
          job lights up amber in the queue list. */}
      <QueueStatusModal
        open={queueModalOpen}
        onClose={() => setQueueModalOpen(false)}
        highlightDraftId={selectedDraftId}
      />
    </main>
  );
}
