"use client";

import { PanelRightOpen, X } from "lucide-react";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { ChatInput } from "@/components/chat/chat-input";
import { MessageList } from "@/components/chat/message-list";
import { MessageListSkeleton } from "@/components/chat/message-list-skeleton";
import { VersionSidebar } from "@/components/layout/version-sidebar";
import { useDatasets } from "@/hooks/use-datasets";
import { useAsk, useSessionHistory } from "@/hooks/use-chat";
import { getCurrentUserId } from "@/lib/auth";
import { useChatStore } from "@/store/chat-store";

export default function ChatSessionPage() {
  const userId = getCurrentUserId()!;
  const { sessionId } = useParams<{ sessionId: string }>();
  const { data: history, isLoading } = useSessionHistory(sessionId);
  const { data: datasets } = useDatasets(userId);

  const storeSessionId = useChatStore((s) => s.sessionId);
  const loadSession = useChatStore((s) => s.loadSession);
  const messages = useChatStore((s) => s.messages);
  const attachedDatasetIds = useChatStore((s) => s.attachedDatasetIds);
  const focusedVersionDatasetId = useChatStore((s) => s.focusedVersionDatasetId);
  const versionSidebarOpen = useChatStore((s) => s.versionSidebarOpen);
  const setVersionSidebarOpen = useChatStore((s) => s.setVersionSidebarOpen);
  const askMutation = useAsk(userId);

  // Datasets uploaded mid-conversation via the chat input's attach button -
  // not attached to the session yet (there's no "just attach" endpoint),
  // so they ride along on the next /chat/ask call's dataset_ids instead.
  const [pendingAttach, setPendingAttach] = useState<string[]>([]);

  // Only re-seed the store from the fetched history when navigating to a
  // *different* session than what's already loaded - otherwise a message
  // we just optimistically appended would get clobbered by a stale query
  // result on the next render.
  useEffect(() => {
    if (history && storeSessionId !== history.session_id) {
      loadSession(history.session_id, history.dataset_ids, history.messages);
    }
  }, [history, storeSessionId, loadSession]);

  function handleUpload(ids: string[]) {
    setPendingAttach((prev) => [...prev, ...ids]);
    toast.success(`${ids.length === 1 ? "File" : "Files"} ready - send a message to attach.`);
  }

  function handleSend(question: string) {
    askMutation.mutate({ datasetIds: pendingAttach, question, sessionId });
    setPendingAttach([]);
  }

  const datasetName = (id: string) => datasets?.find((d) => d.id === id)?.name ?? id;
  const attachedNames = attachedDatasetIds.map(datasetName);
  const stillLoading = isLoading && storeSessionId !== sessionId;

  const mainColumn = (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      {attachedNames.length > 0 && (
        <div className="border-b border-border px-4 py-2 text-xs text-muted-foreground">
          Attached: {attachedNames.join(", ")}
        </div>
      )}
      {/* overflow-x-hidden alongside overflow-y-auto is load-bearing, not
          decorative: setting only one axis leaves the other computed to
          "auto" per the CSS overflow spec, so without this a wide code
          block/table inside would silently open its own horizontal
          scrollbar on this whole pane instead of scrolling locally. */}
      <div className="min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
        {stillLoading ? (
          <MessageListSkeleton />
        ) : (
          <MessageList messages={messages} userId={userId} pending={askMutation.isPending} />
        )}
      </div>
      <div className="border-t border-border">
        {pendingAttach.length > 0 && (
          <div className="flex flex-wrap gap-2 px-4 pt-3">
            {pendingAttach.map((id) => (
              <Badge key={id} variant="secondary" className="gap-1 font-normal">
                {datasetName(id)}
                <button
                  onClick={() => setPendingAttach((prev) => prev.filter((x) => x !== id))}
                  aria-label={`Remove ${datasetName(id)}`}
                >
                  <X className="size-3" />
                </button>
              </Badge>
            ))}
          </div>
        )}
        <ChatInput userId={userId} disabled={askMutation.isPending} onSend={handleSend} onUpload={handleUpload} />
      </div>
    </div>
  );

  // The version panel only becomes a resizable ResizablePanel when open -
  // collapsed, it's a plain slim rail (no drag handle, nothing to resize),
  // matching the same two-mode pattern the panel already had before it
  // became resizable.
  if (!versionSidebarOpen) {
    return (
      <div className="flex h-full">
        {mainColumn}
        <div className="flex w-12 shrink-0 flex-col items-center border-l border-border pt-3">
          <Button variant="ghost" size="icon" onClick={() => setVersionSidebarOpen(true)} aria-label="Show versions">
            <PanelRightOpen className="size-4" />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <ResizablePanelGroup direction="horizontal" autoSaveId="chat-versions-layout" className="h-full">
      <ResizablePanel defaultSize={74} minSize={35}>
        {mainColumn}
      </ResizablePanel>
      <ResizableHandle />
      <ResizablePanel defaultSize={26} minSize={18} maxSize={55} className="min-w-0">
        <VersionSidebar
          datasetId={focusedVersionDatasetId}
          userId={userId}
          onCollapse={() => setVersionSidebarOpen(false)}
        />
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
