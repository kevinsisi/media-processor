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

  const [latestDraft, setLatestDraft] = useState<DraftSummary | null>(null);
  const [seedLoading, setSeedLoading] = useState<boolean>(true);
  const [seedError, setSeedError] = useState<string | null>(null);
  const [triggering, setTriggering] = useState<boolean>(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);

  // Initial: pull the project's drafts list and pick the highest-version one.
  // After the first POST /edit we'll learn the new draft id from the
  // analysis polling endpoint (re-fetched) — this seed happens once.
  useEffect(() => {
    let cancelled = false;
    if (!Number.isFinite(projectId)) return;
    (async () => {
      try {
        const drafts = await apiClient.fetchProjectDrafts(validProjectId);
        if (cancelled) return;
        if (drafts.length === 0) {
          setLatestDraft(null);
        } else {
          drafts.sort((a, b) => b.version - a.version);
          setLatestDraft(drafts[0]);
        }
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
  }, [projectId, validProjectId]);

  const polling = useDraftPolling(latestDraft?.id ?? null);
  const draft = polling.data;

  const handleStartEdit = useCallback(
    async (force: boolean) => {
      setTriggering(true);
      setTriggerError(null);
      try {
        await apiClient.triggerProjectEdit(validProjectId, { force });
        // Re-seed with the just-created draft (the API returns 0 when no
        // existing in-flight draft, so fetch the list fresh).
        const drafts = await apiClient.fetchProjectDrafts(validProjectId);
        drafts.sort((a, b) => b.version - a.version);
        setLatestDraft(drafts[0] ?? null);
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
    [validProjectId, polling],
  );

  const status = draft?.status ?? null;
  const showProcessing = status === "pending" || status === "processing";
  const showReady = status === "ready_for_review";
  const showFailed = status === "failed";
  // Queued: POST has returned 202 (or is in flight) but the first draft poll
  // hasn't resolved yet — without this gap state the page snaps back to the
  // "開始剪輯" CTA for a few seconds and looks like the click did nothing.
  const showQueued =
    !draft && !seedLoading && (triggering || latestDraft !== null);
  const showInitial = !seedLoading && !triggering && latestDraft === null;

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

      {showInitial && (
        <section className="edit-card">
          <h2 className="edit-card__title">準備好就開始</h2>
          <p className="edit-card__body">
            AI 會根據腳本與素材的逐字稿、場景、運鏡，挑選最適合的片段並依節奏拼接成
            一支 9:16 / 4:5 / 1:1 的成品影片，並燒入繁體中文字幕。
          </p>
          <div className="edit-card__actions">
            <button
              type="button"
              className="cta cta--primary"
              onClick={() => void handleStartEdit(false)}
              disabled={triggering}
            >
              {triggering ? "排隊中…" : "開始剪輯"}
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
                  {triggering ? "排隊中…" : "重新剪輯"}
                </button>
              </div>
            </div>
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
