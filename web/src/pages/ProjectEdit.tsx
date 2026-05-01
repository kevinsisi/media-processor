import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, apiClient } from "../api/client";
import type { DraftDetail, DraftSummary } from "../api/types";
import { useDraftPolling } from "../hooks/useDraftPolling";
import {
  EDIT_STEP_LABELS,
  labelForCutSource,
  labelForDraftStatus,
  labelForStepState,
} from "../i18n/tags";
import "./ProjectEdit.css";

const EDIT_STEP_ORDER: ("plan" | "cut" | "concat" | "subtitles")[] = [
  "plan",
  "cut",
  "concat",
  "subtitles",
];

// Quick-pick lengths offered alongside the free-form input. Matches the
// IG/TikTok short-form sweet spots; backend clamps the final value to
// the 10–300 s range regardless of what's typed.
const DURATION_PRESETS_S = [30, 60, 90, 120] as const;
const DEFAULT_DURATION_S = 60;
const DURATION_MIN_S = 10;
const DURATION_MAX_S = 300;

function formatTimecode(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function classifyStepState(value: string | undefined): string {
  if (!value) return "pending";
  if (value.startsWith("failed:")) return "failed";
  return value;
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

interface ProgressTrackerProps {
  steps: Record<string, string> | null | undefined;
}

function ProgressTracker({ steps }: ProgressTrackerProps) {
  return (
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
  );
}

interface TimelineStripProps {
  draft: DraftDetail;
  videoRef: React.RefObject<HTMLVideoElement>;
}

function TimelineStrip({ draft, videoRef }: TimelineStripProps) {
  const segments = draft.segments;
  const totalMs = useMemo(
    () =>
      segments.length === 0
        ? 0
        : Math.max(...segments.map((s) => s.on_timeline_end_ms)),
    [segments],
  );
  if (segments.length === 0) {
    return (
      <div className="timeline-strip timeline-strip--empty mono">
        尚無片段
      </div>
    );
  }
  return (
    <ol className="timeline-strip" aria-label="剪輯時間軸">
      {segments.map((seg) => {
        const cls =
          seg.source_kind === "scripted"
            ? "timeline-cell--scripted"
            : "timeline-cell--improv";
        return (
          <li
            key={`seg-${seg.order}`}
            className={`timeline-cell ${cls}`}
          >
            <button
              type="button"
              className="timeline-cell__btn"
              onClick={() => {
                const v = videoRef.current;
                if (!v) return;
                v.currentTime = seg.on_timeline_start_ms / 1000;
                void v.play().catch(() => {});
              }}
            >
              <span className="timeline-cell__order mono">#{seg.order + 1}</span>
              <span className="timeline-cell__range mono">
                {formatTimecode(seg.on_timeline_start_ms)}
                {" → "}
                {formatTimecode(seg.on_timeline_end_ms)}
              </span>
              <span className="timeline-cell__chip">
                {labelForCutSource(seg.source_kind)}
              </span>
              {seg.plan_reason && (
                <span className="timeline-cell__reason" title={seg.plan_reason}>
                  {seg.plan_reason}
                </span>
              )}
              <span className="timeline-cell__total mono">
                {totalMs > 0
                  ? `${Math.round(((seg.on_timeline_end_ms - seg.on_timeline_start_ms) / totalMs) * 100)}%`
                  : ""}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
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
  const [durationSec, setDurationSec] = useState<number>(DEFAULT_DURATION_S);

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
        await apiClient.triggerProjectEdit(validProjectId, {
          force,
          target_duration_seconds: target,
        });
        // Refresh the list and jump to the freshly-created version so the
        // user sees the new render's progress, not the one they just left.
        // Old versions stay in the list — clicking a chip switches back.
        const list = await refreshDrafts();
        setSelectedDraftId(list[0]?.id ?? null);
        polling.refresh();
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
    [validProjectId, polling, durationSec, refreshDrafts],
  );

  const status = draft?.status ?? null;
  const showProcessing = status === "pending" || status === "processing";
  const showReady = status === "ready_for_review";
  const showFailed = status === "failed";
  // Queued: POST has returned 202 (or is in flight) but the first draft poll
  // hasn't resolved yet — without this gap state the page snaps back to the
  // "開始剪輯" CTA for a few seconds and looks like the click did nothing.
  const showQueued =
    !draft && !seedLoading && (triggering || selectedDraftId !== null);
  const showInitial = !seedLoading && !triggering && drafts.length === 0;

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
          <DurationPicker
            value={durationSec}
            onChange={setDurationSec}
            disabled={triggering}
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
            <DurationPicker
              value={durationSec}
              onChange={setDurationSec}
              disabled={triggering}
            />
            <TimelineStrip
              draft={draft}
              videoRef={videoRef as React.RefObject<HTMLVideoElement>}
            />
            {draft.cut_plan?.notes && (
              <p className="edit-card__hint mono">「{draft.cut_plan.notes}」</p>
            )}
            {draft.cut_plan?.used_fallback && (
              <p className="edit-hint">
                已切換為備用規劃（{draft.cut_plan.fallback_reason || "未知原因"}）。
              </p>
            )}
          </section>
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
    </main>
  );
}
