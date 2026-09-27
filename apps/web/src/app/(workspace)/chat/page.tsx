"use client";

import { useRouter } from "next/navigation";

import { ChatInput } from "@/components/chat/chat-input";
import { DatasetPicker } from "@/components/chat/dataset-picker";
import { getCurrentUserId } from "@/lib/auth";
import { useAsk } from "@/hooks/use-chat";
import { useChatStore } from "@/store/chat-store";

export default function NewChatPage() {
  const userId = getCurrentUserId()!;
  const router = useRouter();
  const selectedDatasetIds = useChatStore((s) => s.selectedDatasetIds);
  const toggleSelectedDataset = useChatStore((s) => s.toggleSelectedDataset);
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

  return (
    <div className="mx-auto flex h-full max-w-2xl flex-col items-center justify-center gap-6 px-4">
      <div className="w-full space-y-3 text-center">
        <h1 className="text-2xl font-semibold">Start a new chat</h1>
        <p className="text-sm text-muted-foreground">Select one or more datasets, then ask a question.</p>
      </div>
      <div className="w-full">
        <DatasetPicker userId={userId} selectedIds={selectedDatasetIds} onToggle={toggleSelectedDataset} />
      </div>
      <div className="w-full">
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
