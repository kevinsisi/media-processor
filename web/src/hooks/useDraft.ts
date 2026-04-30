import { apiClient } from "../api/client";
import { useApi } from "./useApi";

export function useDraft(id: number | null | undefined) {
  return useApi(
    id == null ? null : () => apiClient.fetchDraft(id),
    [id ?? null],
  );
}

export function useAsset(id: number | null | undefined) {
  return useApi(
    id == null ? null : () => apiClient.fetchAsset(id),
    [id ?? null],
  );
}
