import { useCallback, useState } from "react";
import { apiClient } from "../api/client";
import type { ReviewCreate, ReviewOut } from "../api/types";

export interface UseReviewMutation {
  submit: (payload: ReviewCreate) => Promise<ReviewOut>;
  submitting: boolean;
  error: Error | null;
  result: ReviewOut | null;
  reset: () => void;
}

export function useReviewMutation(): UseReviewMutation {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [result, setResult] = useState<ReviewOut | null>(null);

  const submit = useCallback(async (payload: ReviewCreate) => {
    setSubmitting(true);
    setError(null);
    try {
      const out = await apiClient.postReview(payload);
      setResult(out);
      return out;
    } catch (e) {
      const err = e instanceof Error ? e : new Error(String(e));
      setError(err);
      throw err;
    } finally {
      setSubmitting(false);
    }
  }, []);

  const reset = useCallback(() => {
    setError(null);
    setResult(null);
  }, []);

  return { submit, submitting, error, result, reset };
}
