// v0.25.0 — RQ queue inspector + cancel modal.
//
// Renders the current running job (if any) and the ordered queue,
// polling every 3 s while the modal is open. A "取消" button next to
// each queued job calls DELETE /queue/jobs/{id}; the modal optimistic-
// removes the row and refreshes shortly after for the authoritative
// server view.
//
// Used by the header badge (<QueueStatusBadge>) AND by ProjectEdit's
// "查看排隊" button — both pop the same modal.

import { useCallback, useEffect, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { QueueJobItem, QueueStatusOut } from "../api/types";
import "./QueueStatusModal.css";

interface QueueStatusModalProps {
  open: boolean;
  onClose: () => void;
  // When set, the modal highlights the row whose draft_id matches so
  // the user can find "their" job in a long queue.
  highlightDraftId?: number | null;
}

const POLL_INTERVAL_MS = 3000;

const KIND_LABEL: Record<string, string> = {
  analyze: "分析",
  translate: "翻譯",
  render: "剪輯",
  export: "匯出",
  bgm: "AI 配樂",
  unknown: "其他",
};

function fmtElapsed(elapsedSec: number | null): string {
  if (elapsedSec == null) return "";
  const s = Math.floor(elapsedSec);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r > 0 ? `${m} 分 ${r} 秒` : `${m} 分`;
}

function fmtWaiting(enqueuedAt: string | null): string {
  if (!enqueuedAt) return "";
  const ms = Date.now() - new Date(enqueuedAt).getTime();
  if (ms < 0) return "";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `已排 ${s} 秒`;
  const m = Math.floor(s / 60);
  return `已排 ${m} 分`;
}

function jobLabel(item: QueueJobItem): string {
  const kindZh = KIND_LABEL[item.kind] ?? item.kind;
  if (item.project_name) {
    return `${item.project_name} 的${kindZh}`;
  }
  if (item.project_id != null) {
    return `專案 #${item.project_id} 的${kindZh}`;
  }
  return kindZh;
}

export default function QueueStatusModal({
  open,
  onClose,
  highlightDraftId,
}: QueueStatusModalProps) {
  const [data, setData] = useState<QueueStatusOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [, tick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const status = await apiClient.getQueueStatus();
      setData(status);
      setError(null);
    } catch (exc) {
      const msg = exc instanceof ApiError ? exc.message : String(exc);
      setError(msg);
    }
  }, []);

  // Poll while open. Also refresh on open.
  useEffect(() => {
    if (!open) return;
    refresh();
    const id = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [open, refresh]);

  // Drive the elapsed-time tickers without re-fetching the API every
  // second. The bumped state forces a re-render so ``fmtElapsed`` /
  // ``fmtWaiting`` recompute against ``Date.now()``.
  useEffect(() => {
    if (!open) return;
    const id = window.setInterval(() => tick((n) => n + 1), 1000);
    return () => window.clearInterval(id);
  }, [open]);

  const onCancel = useCallback(
    async (jobId: string) => {
      setCancelling(jobId);
      try {
        await apiClient.cancelQueuedJob(jobId);
        // Optimistic local drop, then re-fetch for canonical state.
        setData((prev) =>
          prev
            ? { ...prev, queued: prev.queued.filter((q) => q.job_id !== jobId) }
            : prev,
        );
        await refresh();
      } catch (exc) {
        const msg = exc instanceof ApiError ? exc.message : String(exc);
        setError(`取消失敗：${msg}`);
      } finally {
        setCancelling(null);
      }
    },
    [refresh],
  );

  if (!open) return null;

  const running = data?.running ?? null;
  const queued = data?.queued ?? [];

  return (
    <div
      className="queue-modal__backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="排隊狀態"
      onClick={(ev) => {
        if (ev.target === ev.currentTarget) onClose();
      }}
    >
      <div className="queue-modal">
        <header className="queue-modal__head">
          <h2 className="queue-modal__title">排隊狀態</h2>
          <button
            type="button"
            className="queue-modal__close"
            onClick={onClose}
            aria-label="關閉"
          >
            ×
          </button>
        </header>

        {error && <p className="queue-modal__error">{error}</p>}

        <section className="queue-modal__section">
          <h3 className="queue-modal__section-title">目前處理中</h3>
          {running ? (
            <div
              className={
                highlightDraftId != null && running.draft_id === highlightDraftId
                  ? "queue-modal__row queue-modal__row--running queue-modal__row--mine"
                  : "queue-modal__row queue-modal__row--running"
              }
            >
              <div className="queue-modal__row-main">
                <span className="queue-modal__pulse" aria-hidden="true" />
                <span className="queue-modal__row-label">{jobLabel(running)}</span>
              </div>
              <div className="queue-modal__row-meta">
                {running.elapsed_s != null
                  ? `已進行 ${fmtElapsed(running.elapsed_s)}`
                  : "已開始"}
              </div>
            </div>
          ) : (
            <p className="queue-modal__empty">— 沒有任務在跑</p>
          )}
        </section>

        <section className="queue-modal__section">
          <h3 className="queue-modal__section-title">
            排隊中（{queued.length}）
          </h3>
          {queued.length === 0 ? (
            <p className="queue-modal__empty">— 排隊清空</p>
          ) : (
            <ol className="queue-modal__list">
              {queued.map((job) => {
                const isMine =
                  highlightDraftId != null && job.draft_id === highlightDraftId;
                return (
                  <li
                    key={job.job_id}
                    className={
                      isMine
                        ? "queue-modal__row queue-modal__row--mine"
                        : "queue-modal__row"
                    }
                  >
                    <div className="queue-modal__row-main">
                      <span className="queue-modal__row-pos">
                        #{(job.position ?? 0) + 1}
                      </span>
                      <span className="queue-modal__row-label">
                        {jobLabel(job)}
                      </span>
                      {isMine && (
                        <span className="queue-modal__row-mine-tag">你的任務</span>
                      )}
                    </div>
                    <div className="queue-modal__row-meta">
                      {fmtWaiting(job.enqueued_at)}
                    </div>
                    <button
                      type="button"
                      className="queue-modal__cancel"
                      onClick={() => onCancel(job.job_id)}
                      disabled={cancelling === job.job_id}
                    >
                      {cancelling === job.job_id ? "取消中…" : "取消"}
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </section>
      </div>
    </div>
  );
}
