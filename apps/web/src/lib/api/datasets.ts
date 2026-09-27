import { request } from "./client";
import type { DatasetSummary, ExportFormat } from "@/lib/types/api";

export async function uploadDataset(file: File, userId: string): Promise<{ datasets: DatasetSummary[] }> {
  const form = new FormData();
  form.append("file", file);
  form.append("user_id", userId);
  const res = await fetch("/api/datasets/upload", { method: "POST", body: form });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export function listDatasets(userId: string): Promise<{ datasets: DatasetSummary[] }> {
  return request(`/datasets?${new URLSearchParams({ user_id: userId })}`);
}

export function getDataset(datasetId: string): Promise<DatasetSummary> {
  return request(`/datasets/${datasetId}`);
}

/** Not fetched - used directly as an <a href>/download link, same as the old app. */
export function exportUrl(datasetId: string, format: ExportFormat, versionId?: string): string {
  const params = new URLSearchParams({ format });
  if (versionId) params.set("version_id", versionId);
  return `/api/datasets/${datasetId}/export?${params.toString()}`;
}
