import { useCallback, useEffect, useRef, useState } from "react";
import { apiClient } from "../api/client";
import type { ProjectAnalysisOut } from "../api/types";

const FAST_INTERVAL_MS = 3_000;
const SLOW_INTERVAL_MS = 10_000;
// Once everything settles, keep polling at the slow cadence for this long
// in case the operator just edited the script and is waiting for re-cov.
const SETTLE_TAIL_MS = 60_000;

export interface UseAssetPolling {
  data: ProjectAnalysisOut | null;
  error: Error | null;
  loading: boolean;
  pollIntervalMs: number;
  isPolling: boolean;
  refresh: () => void;
}

function isAnyAssetAnalyzing(data: ProjectAnalysisOut | null): boolean {
  if (!data) return false;
  return data.assets.some((a) => a.status === "analyzing");
}

export function useAssetPolling(projectId: number | null): UseAssetPolling {
  const [data, setData] = useState<ProjectAnalysisOut | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState<boolean>(projectId !== null);
  const [pollIntervalMs, setPollIntervalMs] = useState<number>(FAST_INTERVAL_MS);
  const [isPolling, setIsPolling] = useState<boolean>(true);

  const settleStartRef = useRef<number | null>(null);
  const generationRef = useRef(0);
  const inFlightGenerationRef = useRef<number | null>(null);

  const fetchOnce = useCallback(async () => {
    if (projectId === null) return;
    const generation = generationRef.current;
    if (inFlightGenerationRef.current !== null) return;
    inFlightGenerationRef.current = generation;
    try {
      const result = await apiClient.fetchProjectAnalysis(projectId);
      if (generation !== generationRef.current) return;
      setData(result);
      setError(null);

      const analyzing = isAnyAssetAnalyzing(result);
      if (analyzing) {
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
      if (generation !== generationRef.current) return;
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      if (inFlightGenerationRef.current === generation) {
        inFlightGenerationRef.current = null;
      }
      if (generation !== generationRef.current) return;
      setLoading(false);
    }
  }, [projectId]);

  // Reset everything on projectId change.
  useEffect(() => {
    const generation = generationRef.current + 1;
    generationRef.current = generation;
    inFlightGenerationRef.current = null;
    settleStartRef.current = null;
    if (projectId === null) {
      setIsPolling(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    setIsPolling(true);
    setPollIntervalMs(FAST_INTERVAL_MS);
    void fetchOnce();
    return () => {
      if (generationRef.current === generation) generationRef.current += 1;
      if (inFlightGenerationRef.current === generation) {
        inFlightGenerationRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Recurring poll.
  useEffect(() => {
    if (!isPolling || projectId === null) return;
    const handle = window.setInterval(() => {
      void fetchOnce();
    }, pollIntervalMs);
    return () => window.clearInterval(handle);
  }, [isPolling, pollIntervalMs, fetchOnce, projectId]);

  const refresh = useCallback(() => {
    settleStartRef.current = null;
    setPollIntervalMs(FAST_INTERVAL_MS);
    setIsPolling(true);
    void fetchOnce();
  }, [fetchOnce]);

  return { data, error, loading, pollIntervalMs, isPolling, refresh };
}
