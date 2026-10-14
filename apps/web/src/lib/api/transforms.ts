import { request } from "./client";
import type { DatasetSampleResponse, VersionSummary, VersionsResponse } from "@/lib/types/api";

export function confirmTransform(pendingId: string): Promise<VersionSummary> {
  return request(`/transforms/${pendingId}/confirm`, { method: "POST" });
}

export function rejectTransform(pendingId: string): Promise<{ id: string; status: string }> {
  return request(`/transforms/${pendingId}/reject`, { method: "POST" });
}

export function listVersions(datasetId: string): Promise<VersionsResponse> {
  return request(`/datasets/${datasetId}/versions`);
}

export function getDatasetSample(datasetId: string, limit = 30): Promise<DatasetSampleResponse> {
  return request(`/datasets/${datasetId}/sample?${new URLSearchParams({ limit: String(limit) })}`);
}

export function setActiveVersion(datasetId: string, versionId: string): Promise<unknown> {
  return request(`/datasets/${datasetId}/active-version`, {
    method: "POST",
    body: JSON.stringify({ version_id: versionId }),
  });
}
