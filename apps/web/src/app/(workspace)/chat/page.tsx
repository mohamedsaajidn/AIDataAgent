"use client";

import { useRouter } from "next/navigation";

import { ChatInput } from "@/components/chat/chat-input";
import { DatasetPicker } from "@/components/chat/dataset-picker";
import { MessageList } from "@/components/chat/message-list";
import { getCurrentUserId } from "@/lib/auth";
import { useAsk } from "@/hooks/use-chat";
import { useChatStore } from "@/store/chat-store";

export default function NewChatPage() {
  const userId = getCurrentUserId()!;
  const router = useRouter();
  const selectedDatasetIds = useChatStore((s) => s.selectedDatasetIds);
  const toggleSelectedDataset = useChatStore((s) => s.toggleSelectedDataset);
  const messages = useChatStore((s) => s.messages);
  const askMutation = useAsk(userId);

  async function handleSend(question: string) {
    if (selectedDatasetIds.length === 0) return;
    const res = await askMutation.mutateAsync({
      datasetIds: selectedDatasetIds,
      question,
      sessionId: null,
    });
    router.push(`/chat/${res.session_id}`);
  }

  // useAsk's mutationFn appends the user's question to the store
  // synchronously, before the network call even resolves - but this page
  // used to keep rendering the dataset-picker screen regardless, with
  // nowhere for that message (or a "working" indicator) to show up. The
  // first question in a brand new chat looked like nothing had happened at
  // all until the full answer came back and the page had already navigated
  // to /chat/[sessionId] - every question after the first showed the
  // working indicator correctly because it was asked from that page
  // instead, which always rendered MessageList. Switching into the same
  // layout the moment there's anything to show closes that gap.
  if (messages.length > 0 || askMutation.isPending) {
    return (
      <div className="flex h-full flex-col">
        <div className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
          <MessageList messages={messages} userId={userId} pending={askMutation.isPending} />
        </div>
        <div className="border-t border-border">
          <ChatInput userId={userId} disabled onSend={() => {}} />
        </div>
      </div>
    );
  }

  return (
    // A vertically-centered, height-unconstrained column (the previous
    // layout) looks fine with a couple of datasets, but as the dataset list
    // grows past what fits on screen, centering an over-tall column pushes
    // it out equally both ways - the heading off the top, the chat input
    // off the bottom, with no scrollbar anywhere to reach either. Fixed
    // header/footer with only the dataset list scrolling in between
    // (min-h-0 on the flex-1 middle section is load-bearing, not
    // decorative - the same pattern the actual conversation view and the
    // version sidebar already use for the identical reason) keeps both
    // always on screen regardless of how many datasets exist.
    <div className="mx-auto flex h-full w-full max-w-2xl flex-col px-4">
      <div className="shrink-0 space-y-3 pt-10 pb-6 text-center">
        <h1 className="text-2xl font-semibold">Start a new chat</h1>
        <p className="text-sm text-muted-foreground">Select one or more datasets, then ask a question.</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <DatasetPicker userId={userId} selectedIds={selectedDatasetIds} onToggle={toggleSelectedDataset} />
      </div>
      <div className="shrink-0 pt-4 pb-6">
        <ChatInput
          userId={userId}
          disabled={askMutation.isPending}
          sendDisabled={selectedDatasetIds.length === 0 || askMutation.isPending}
          onSend={handleSend}
          onUpload={(ids) => ids.forEach(toggleSelectedDataset)}
        />
        {selectedDatasetIds.length === 0 && (
          <p className="px-4 text-xs text-muted-foreground">
            Select at least one dataset, or attach a new one, to start a chat.
          </p>
        )}
      </div>
    </div>
  );
}
