import { useCallback, useEffect, useRef, useState } from "react";

export interface UseApiState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  refetch: () => void;
}

// Generic loading/error/data hook for GET-style endpoints.
// Pass `null` as `fetcher` to skip the call (e.g. waiting for an id).
export function useApi<T>(
  fetcher: (() => Promise<T>) | null,
  deps: ReadonlyArray<unknown>,
): UseApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState<boolean>(fetcher !== null);
  const [tick, setTick] = useState(0);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  useEffect(() => {
    const current = fetcherRef.current;
    if (!current) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    current()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        setError(null);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const refetch = useCallback(() => setTick((n) => n + 1), []);

  return { data, error, loading, refetch };
}
