import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../api/client";
import type { DraftDetail } from "../api/types";

const FAST_INTERVAL_MS = 3_000;
const SLOW_INTERVAL_MS = 10_000;
const SETTLE_TAIL_MS = 60_000;

export interface UseDraftPolling {
  data: DraftDetail | null;
  error: Error | null;
  loading: boolean;
  pollIntervalMs: number;
  isPolling: boolean;
  refresh: () => void;
  // v0.16 — apply a server-fresh DraftDetail (e.g. the response body of
  // a synchronous PATCH that mutated the draft) without waiting for the
  // next poll tick. Resets the settle timer so polling resumes at the
  // fast interval if the new state is back to processing.
  applyDraft: (next: DraftDetail) => void;
}

function isProcessing(data: DraftDetail | null): boolean {
  return data?.status === "processing" || data?.status === "pending";
}

export function useDraftPolling(draftId: number | null): UseDraftPolling {
  const [data, setData] = useState<DraftDetail | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState<boolean>(draftId !== null);
  const [pollIntervalMs, setPollIntervalMs] = useState<number>(FAST_INTERVAL_MS);
  const [isPolling, setIsPolling] = useState<boolean>(true);

  const settleStartRef = useRef<number | null>(null);
  const cancelRef = useRef<{ cancelled: boolean }>({ cancelled: false });

  const fetchOnce = useCallback(async () => {
    if (draftId === null) return;
    try {
      const result = await apiClient.fetchDraft(draftId);
      if (cancelRef.current.cancelled) return;
      setData(result);
      setError(null);

      if (isProcessing(result)) {
        settleStartRef.current = null;
        setPollIntervalMs(FAST_INTERVAL_MS);
        setIsPolling(true);
      } else {
        if (settleStartRef.current === null) {
          settleStartRef.current = Date.now();
        }
        const settledFor = Date.now() - settleStartRef.current;
        if (settledFor >= SETTLE_TAIL_MS) {
          setIsPolling(false);
        } else {
          setPollIntervalMs(SLOW_INTERVAL_MS);
          setIsPolling(true);
        }
      }
    } catch (err) {
      if (cancelRef.current.cancelled) return;
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (cancelRef.current.cancelled) return;
      setLoading(false);
    }
  }, [draftId]);

  useEffect(() => {
    cancelRef.current = { cancelled: false };
    settleStartRef.current = null;
    if (draftId === null) {
      setIsPolling(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    setIsPolling(true);
    setPollIntervalMs(FAST_INTERVAL_MS);
    void fetchOnce();
    return () => {
      cancelRef.current.cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftId]);

  useEffect(() => {
    if (!isPolling || draftId === null) return;
    const handle = window.setInterval(() => {
      void fetchOnce();
    }, pollIntervalMs);
    return () => window.clearInterval(handle);
  }, [isPolling, pollIntervalMs, fetchOnce, draftId]);

  const refresh = useCallback(() => {
    settleStartRef.current = null;
    setPollIntervalMs(FAST_INTERVAL_MS);
    setIsPolling(true);
    void fetchOnce();
  }, [fetchOnce]);

  const applyDraft = useCallback((next: DraftDetail) => {
    setData(next);
    setError(null);
    settleStartRef.current = null;
    setPollIntervalMs(FAST_INTERVAL_MS);
    setIsPolling(true);
  }, []);

  return { data, error, loading, pollIntervalMs, isPolling, refresh, applyDraft };
}
