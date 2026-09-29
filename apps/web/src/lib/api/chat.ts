import { request, requestText } from "./client";
import type { AskRequest, AskResponse, SessionHistoryResponse, SessionSummary } from "@/lib/types/api";

export function askQuestion(params: {
  datasetIds: string[];
  question: string;
  userId: string;
  sessionId?: string | null;
  useLegacyQaGraph?: boolean;
}): Promise<AskResponse> {
  const body: AskRequest = {
    dataset_ids: params.datasetIds,
    question: params.question,
    user_id: params.userId,
    session_id: params.sessionId ?? null,
    use_legacy_qa_graph: params.useLegacyQaGraph ?? false,
  };
  return request("/chat/ask", { method: "POST", body: JSON.stringify(body) });
}

export function listSessions(userId: string): Promise<{ sessions: SessionSummary[] }> {
  return request(`/chat/sessions?${new URLSearchParams({ user_id: userId })}`);
}

export function getSessionHistory(sessionId: string): Promise<SessionHistoryResponse> {
  return request(`/chat/sessions/${sessionId}`);
}

export function getMessageArtifact(messageId: string): Promise<string> {
  return requestText(`/chat/messages/${messageId}/artifact`);
}
