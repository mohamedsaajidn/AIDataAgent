import { request } from "./client";
import type { DatasetSampleResponse, VersionSummary, VersionsResponse } from "@/lib/types/api";

/** RBAC-gated - requires the editor role server-side. `userId` is a
 * required query param (not a header/body field) to match the backend's
 * confirm/active-version signature exactly. */
export function confirmTransform(pendingId: string, userId: string): Promise<VersionSummary> {
  return request(`/transforms/${pendingId}/confirm?${new URLSearchParams({ user_id: userId })}`, {
    method: "POST",
  });
}

/** Deliberately NOT RBAC-gated server-side - rejecting only discards a
 * preview, so no user_id is required here. */
export function rejectTransform(pendingId: string): Promise<{ id: string; status: string }> {
  return request(`/transforms/${pendingId}/reject`, { method: "POST" });
}

export function listVersions(datasetId: string): Promise<VersionsResponse> {
  return request(`/datasets/${datasetId}/versions`);
}

export function getDatasetSample(datasetId: string, limit = 30): Promise<DatasetSampleResponse> {
  return request(`/datasets/${datasetId}/sample?${new URLSearchParams({ limit: String(limit) })}`);
}

/** RBAC-gated (editor only) - same query-param pattern as confirm. */
export function setActiveVersion(datasetId: string, versionId: string, userId: string): Promise<unknown> {
  return request(`/datasets/${datasetId}/active-version?${new URLSearchParams({ user_id: userId })}`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId }),
  });
}
