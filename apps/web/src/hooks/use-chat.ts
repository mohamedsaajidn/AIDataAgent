import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { askQuestion, getMessageArtifact, getSessionHistory, listSessions } from "@/lib/api/chat";
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

/** A chart/EDA artifact is fetched on its own, only for a message that
 * actually has one - see AskResponse.has_artifact's doc comment for why
 * this isn't inlined into the message itself. Cached per messageId, so
 * re-rendering (or briefly scrolling a message out of view and back) never
 * re-fetches a multi-MB artifact it already has. */
export function useMessageArtifact(messageId: string | null | undefined, hasArtifact: boolean | undefined) {
  return useQuery({
    queryKey: ["artifact", messageId],
    queryFn: () => getMessageArtifact(messageId as string),
    enabled: !!messageId && !!hasArtifact,
    staleTime: Infinity,
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
