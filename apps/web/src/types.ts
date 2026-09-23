export interface ColumnInfo {
  name: string
  pandas_dtype: string
  inferred_type: string
  null_pct: number
  distinct_count: number
  cardinality_ratio: number
  sample_values: unknown[]
  is_pii: boolean
  pii_entities: string[]
  description: string | null
}

export interface DatasetSummary {
  id: string
  name: string
  original_filename: string
  created_at: string | null
  row_count: number | null
  column_count: number | null
  columns: ColumnInfo[]
}

export interface DiffSummary {
  columns_added: string[]
  columns_removed: string[]
  row_count_before: number
  row_count_after: number
  column_count_before: number
  column_count_after: number
  summary: string
}

export interface AskResponse {
  session_id: string
  user_id: string
  dataset_ids: string[]
  resolved_dataset_id: string | null
  answer: string
  route: string
  code: string | null
  result: unknown
  error: string | null
  latency_ms: number
  prompt_tokens: number
  completion_tokens: number
  attempts: number
  artifact_html: string | null
  diff: DiffSummary | null
  pending_transform_id: string | null
  remembered_fact: string | null
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  route: string | null
  code: string | null
  result: unknown
  error: string | null
  latency_ms: number | null
  prompt_tokens: number | null
  completion_tokens: number | null
  attempts: number | null
  artifact_html: string | null
  pending_transform_id: string | null
  created_at: string | null
}

export interface SessionHistory {
  session_id: string
  dataset_ids: string[]
  user_id: string | null
  messages: ChatMessage[]
}

export interface DatasetVersionSummary {
  id: string
  dataset_id: string
  version_number: number
  row_count: number
  column_count: number
  description: string | null
  source: string
  parent_version_id: string | null
  diff_summary: string | null
  generated_code: string | null
  created_at: string | null
}

export interface VersionsResponse {
  active_version_id: string | null
  versions: DatasetVersionSummary[]
}

export interface SessionSummary {
  session_id: string
  dataset_ids: string[]
  created_at: string | null
  message_count: number
  last_question: string | null
}
