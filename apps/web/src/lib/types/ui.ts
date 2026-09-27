import type { AskResult, ChatRoute, DiffSummary, PendingTransformStatus } from "@/lib/types/api";

/** Component-facing shape (camelCase) - the translation layer between the
 * wire format (ChatMessageRecord/AskResponse, snake_case) and the UI lives
 * in store/chat-store.ts's fromHistory()/fromAskResponse() helpers. */
export interface DisplayMessage {
  role: "user" | "assistant";
  content: string;
  route?: ChatRoute | null;
  code?: string | null;
  result?: AskResult;
  artifactHtml?: string | null;
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
