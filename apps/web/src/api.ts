import type { AskResponse, DatasetSummary, SessionHistory, SessionSummary, VersionsResponse } from './types'

const BASE = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `${res.status} ${res.statusText}`)
  }
  return res.json()
}

export async function uploadDataset(file: File, userId: string): Promise<{ datasets: DatasetSummary[] }> {
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', userId)
  const res = await fetch(`${BASE}/datasets/upload`, { method: 'POST', body: form })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `${res.status} ${res.statusText}`)
  }
  return res.json()
}

export function listDatasets(userId: string): Promise<{ datasets: DatasetSummary[] }> {
  return request(`/datasets?${new URLSearchParams({ user_id: userId })}`)
}

export function listSessions(userId: string): Promise<{ sessions: SessionSummary[] }> {
  return request(`/chat/sessions?${new URLSearchParams({ user_id: userId })}`)
}

export function getDataset(datasetId: string): Promise<DatasetSummary> {
  return request(`/datasets/${datasetId}`)
}

export function askQuestion(params: {
  datasetIds: string[]
  question: string
  userId: string
  sessionId?: string | null
}): Promise<AskResponse> {
  return request('/chat/ask', {
    method: 'POST',
    body: JSON.stringify({
      dataset_ids: params.datasetIds,
      question: params.question,
      user_id: params.userId,
      session_id: params.sessionId ?? null,
    }),
  })
}

export function getSessionHistory(sessionId: string): Promise<SessionHistory> {
  return request(`/chat/sessions/${sessionId}`)
}

export function confirmTransform(pendingId: string): Promise<unknown> {
  return request(`/transforms/${pendingId}/confirm`, { method: 'POST' })
}

export function rejectTransform(pendingId: string): Promise<unknown> {
  return request(`/transforms/${pendingId}/reject`, { method: 'POST' })
}

export function listVersions(datasetId: string): Promise<VersionsResponse> {
  return request(`/datasets/${datasetId}/versions`)
}

export function setActiveVersion(datasetId: string, versionId: string): Promise<unknown> {
  return request(`/datasets/${datasetId}/active-version`, {
    method: 'POST',
    body: JSON.stringify({ version_id: versionId }),
  })
}

export function exportUrl(datasetId: string, format: 'csv' | 'xlsx' | 'parquet', versionId?: string): string {
  const params = new URLSearchParams({ format })
  if (versionId) params.set('version_id', versionId)
  return `${BASE}/datasets/${datasetId}/export?${params.toString()}`
}
