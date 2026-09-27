import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { askQuestion, getSessionHistory, listSessions } from "@/lib/api/chat";
import { useChatStore } from "@/store/chat-store";

export function useSessions(userId: string) {
  return useQuery({
    queryKey: ["sessions", userId],
    queryFn: () => listSessions(userId),
    select: (data) => data.sessions,
    enabled: !!userId,
  });
}

export function useSessionHistory(sessionId: string | null) {
  return useQuery({
    queryKey: ["session-history", sessionId],
    queryFn: () => getSessionHistory(sessionId as string),
    enabled: !!sessionId,
  });
}

/** Optimistically appends the user's message, calls the (non-streaming)
 * backend, then appends the assistant reply - see the "Streaming scope"
 * decision in the frontend rewrite plan: this stays a single blocking
 * request, ChatInput/WorkingIndicator just render a "working" state for
 * its duration rather than faking token streaming. */
export function useAsk(userId: string) {
  const queryClient = useQueryClient();
  const appendUserMessage = useChatStore((s) => s.appendUserMessage);
  const appendAssistantMessage = useChatStore((s) => s.appendAssistantMessage);
  const appendErrorMessage = useChatStore((s) => s.appendErrorMessage);

  return useMutation({
    mutationFn: (params: { datasetIds: string[]; question: string; sessionId: string | null }) => {
      appendUserMessage(params.question);
      return askQuestion({ ...params, userId });
    },
    onSuccess: (res) => {
      appendAssistantMessage(res);
      queryClient.invalidateQueries({ queryKey: ["sessions", userId] });
      if (res.route === "transform" || res.route === "join") {
        queryClient.invalidateQueries({ queryKey: ["versions", res.resolved_dataset_id] });
      }
    },
    onError: (err: Error) => {
      appendErrorMessage(err.message);
    },
  });
}
