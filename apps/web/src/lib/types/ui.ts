import type { AskResult, ChatRoute, DiffSummary, PendingTransformStatus } from "@/lib/types/api";

/** Component-facing shape (camelCase) - the translation layer between the
 * wire format (ChatMessageRecord/AskResponse, snake_case) and the UI lives
 * in store/chat-store.ts's fromHistory()/fromAskResponse() helpers. */
export interface DisplayMessage {
  /** Missing for a message appended live before the server round-trip
   * resolves - always present once persisted (both a fresh ask response
   * and reloaded history carry it). Needed to fetch this message's
   * artifact on demand, see hasArtifact below. */
  id?: string;
  role: "user" | "assistant";
  content: string;
  route?: ChatRoute | null;
  code?: string | null;
  result?: AskResult;
  /** Whether this message has a chart/EDA artifact - fetched separately
   * (useMessageArtifact) by `id` rather than carried inline here. See
   * AskResponse.has_artifact's doc comment for why: a chart/EDA artifact
   * is several MB, sometimes 10+MB for a multi-chart turn, and inlining
   * it into every message in every history load doesn't scale. */
  hasArtifact?: boolean;
  diff?: DiffSummary | null;
  pendingTransformId?: string | null;
  /** Only known for a message reloaded from session history (see
   * ChatMessageRecord.pending_transform_status) - undefined for a message
   * just appended live this session, which TransformCard treats the same
   * as "pending" since that's the only state it can be in yet. */
  pendingTransformStatus?: PendingTransformStatus | null;
  rememberedFact?: string | null;
  error?: string | null;
  createdAt?: string | null;
}
