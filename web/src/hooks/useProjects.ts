import { apiClient } from "../api/client";
import { useApi } from "./useApi";

export function useProjects() {
  return useApi(() => apiClient.fetchProjects(), []);
}

export function useProject(id: number | null | undefined) {
  return useApi(
    id == null ? null : () => apiClient.fetchProject(id),
    [id ?? null],
  );
}

export function useProjectDrafts(id: number | null | undefined) {
  return useApi(
    id == null ? null : () => apiClient.fetchProjectDrafts(id),
    [id ?? null],
  );
}
