// Mirrors the FastAPI backend's actual JSON contract 1:1 (apps/api/src/api/routes/*)
// - kept in snake_case to match the wire format exactly; translate to
// camelCase only at component-prop boundaries where it reads better, never
// silently rename a field here (this file IS the contract).

export type InferredType = "id" | "categorical" | "continuous" | "datetime" | "boolean" | "text";

export interface ColumnProfile {
  name: string;
  pandas_dtype: string;
  inferred_type: InferredType;
  /** Fraction 0-1, NOT a percentage - multiply by 100 only when displaying. */
  null_pct: number;
  distinct_count: number;
  cardinality_ratio: number;
  sample_values: (string | number | boolean | null)[];
  is_pii: boolean;
  pii_entities: string[] | null;
  description: string | null;
}

export interface DatasetSummary {
  id: string;
  name: string;
  original_filename: string;
  uploaded_by_user_id: string | null;
  created_at: string | null;
  row_count: number | null;
  column_count: number | null;
  columns: ColumnProfile[];
}

export interface DiffSummary {
  columns_added: string[];
  columns_removed: string[];
  row_count_before: number;
  row_count_after: number;
  column_count_before: number;
  column_count_after: number;
  summary: string;
}

/** The sandbox's dataframe-preview result shape (see tools/pii.py) - one of
 * several shapes `AskResponse.result` can take alongside a bare scalar,
 * list, or dict. */
export interface DataframeResult {
  type: "dataframe";
  columns: string[];
  preview: Record<string, unknown>[];
  row_count?: number;
}

export type AskResult = DataframeResult | Record<string, unknown> | unknown[] | string | number | boolean | null;

export type ChatRoute =
  | "sql"
  | "pandas"
  | "chart"
  | "chart_freehand"
  | "eda"
  | "transform"
  | "join"
  | "clarify"
  | "chat"
  | string;

export interface AskRequest {
  dataset_ids: string[];
  question: string;
  user_id: string;
  session_id?: string | null;
  use_legacy_qa_graph?: boolean;
}

export interface AskResponse {
  session_id: string;
  /** The newly-created assistant message's id - use with
   * GET /chat/messages/{message_id}/artifact when has_artifact is true. */
  message_id: string;
  user_id: string;
  dataset_ids: string[];
  resolved_dataset_id: string | null;
  answer: string;
  route: ChatRoute;
  code: string | null;
  result: AskResult;
  error: string | null;
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  attempts: number;
  /** Whether this message has a chart/EDA artifact - fetched separately via
   * GET /chat/messages/{message_id}/artifact rather than inlined here.
   * A chart/EDA artifact is several MB (10+MB for a multi-chart turn) -
   * inlining it in every response referencing the message doesn't scale,
   * confirmed live: one real 40-message chart-heavy session's full history
   * alone measured 84MB, which is the likely cause of intermittent
   * failures specifically on chart-generating turns. */
  has_artifact: boolean;
  diff: DiffSummary | null;
  pending_transform_id: string | null;
  remembered_fact: string | null;
}

export type PendingTransformStatus = "pending" | "confirmed" | "rejected";

export interface ChatMessageRecord {
  /** Needed to fetch this message's artifact on demand - see
   * AskResponse.has_artifact's doc comment. */
  id: string;
  role: "user" | "assistant";
  content: string;
  route: ChatRoute | null;
  code: string | null;
  result: AskResult;
  error: string | null;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  attempts: number | null;
  has_artifact: boolean;
  pending_transform_id: string | null;
  diff: DiffSummary | null;
  pending_transform_status: PendingTransformStatus | null;
  created_at: string | null;
}

export interface SessionHistoryResponse {
  session_id: string;
  dataset_ids: string[];
  user_id: string | null;
  messages: ChatMessageRecord[];
}

export interface SessionSummary {
  session_id: string;
  dataset_ids: string[];
  created_at: string | null;
  message_count: number;
  last_question: string | null;
}

export type VersionSource = "upload" | "transform" | "join" | string;

export interface VersionSummary {
  id: string;
  dataset_id: string;
  version_number: number;
  row_count: number;
  column_count: number;
  description: string | null;
  source: VersionSource;
  parent_version_id: string | null;
  diff_summary: string | null;
  generated_code: string | null;
  created_at: string | null;
}

export interface VersionsResponse {
  active_version_id: string | null;
  versions: VersionSummary[];
}

export interface DatasetSampleResponse {
  version_id: string;
  version_number: number;
  row_count: number;
  sample_row_count: number;
  columns: string[];
  /** Columns dropped from `columns`/`rows` server-side - see
   * api.services.transforms.get_dataset_sample's docstring on why this is
   * real redaction, not just a frontend hiding convention. */
  pii_columns: string[];
  rows: Record<string, unknown>[];
}

export type ExportFormat = "csv" | "xlsx" | "parquet";

export interface HealthResponse {
  postgres: string;
  minio: string;
  redis: string;
}
