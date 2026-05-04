// v0.25.0 — header badge that opens the queue inspector modal.
//
// Polls /queue/status every 5 s, rendering the queue depth as a chip.
// The chip pulses softly when something is running. Clicking opens
// <QueueStatusModal> (lazy-loaded state — the modal mounts on demand).
//
// The chip is intentionally small: when the queue is empty it's a
// quiet "0", not a green checkmark, so a quick glance shows the
// pipeline is idle without claiming "all good." The colour shift
// signals state at a glance (idle / running / queued).

import { useCallback, useEffect, useState } from "react";
import { ApiError, apiClient } from "../api/client";
import type { QueueStatusOut } from "../api/types";
import QueueStatusModal from "./QueueStatusModal";
import "./QueueStatusBadge.css";

const POLL_INTERVAL_MS = 5000;

export default function QueueStatusBadge() {
  const [status, setStatus] = useState<QueueStatusOut | null>(null);
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const next = await apiClient.getQueueStatus();
      setStatus(next);
    } catch (exc) {
      // Silent — the badge is non-critical. ApiError ignored.
      void (exc instanceof ApiError ? exc : null);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  // When the modal closes, fire one extra refresh so the badge picks
  // up any cancellations made through the modal without waiting for
  // the next poll tick.
  useEffect(() => {
    if (!open) {
      refresh();
    }
  }, [open, refresh]);

  const running = status?.running ?? null;
  const queuedCount = status?.queued.length ?? 0;
  const totalDepth = (running ? 1 : 0) + queuedCount;

  const variant = running
    ? "queue-badge--running"
    : queuedCount > 0
      ? "queue-badge--queued"
      : "queue-badge--idle";

  const label = running ? `處理中 +${queuedCount}` : `排隊 ${queuedCount}`;

  return (
    <>
      <button
        type="button"
        className={`queue-badge ${variant}`}
        onClick={() => setOpen(true)}
        aria-label={`排隊狀態：${label}（共 ${totalDepth} 個任務）`}
        title="點擊查看排隊"
      >
        {running && <span className="queue-badge__pulse" aria-hidden="true" />}
        <span className="queue-badge__label">{label}</span>
      </button>
      <QueueStatusModal open={open} onClose={() => setOpen(false)} />
    </>
  );
}
