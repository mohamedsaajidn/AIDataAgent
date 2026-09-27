"use client";

import { History, PanelRightClose } from "lucide-react";

import { Button } from "@/components/ui/button";
import { VersionTimeline } from "@/components/datasets/version-timeline";
import { EmptyState } from "@/components/common/empty-state";
import { useDataset } from "@/hooks/use-datasets";

/** Brought back per feedback: confirming a transform used to require
 * leaving the chat and going to the Data Explorer to see the resulting
 * version - this mirrors the old app's always-visible right-hand
 * VersionPanel, just collapsible now that the chat column wants the width
 * back by default.
 *
 * This is pure panel *content* - the collapsed-rail state and the resizable
 * width are both owned by the parent page (a ResizablePanel wraps this when
 * open, a plain slim rail replaces it when collapsed), so this component
 * doesn't own its own width/border/collapse-rail markup any more. */
export function VersionSidebar({
  datasetId,
  userId,
  onCollapse,
}: {
  datasetId: string | null;
  userId: string;
  onCollapse: () => void;
}) {
  const { data: dataset } = useDataset(datasetId);

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <div className="min-w-0">
          <p className="text-sm font-medium">Versions</p>
          {dataset && <p className="truncate text-xs text-muted-foreground">{dataset.name}</p>}
        </div>
        <Button variant="ghost" size="icon" onClick={onCollapse} aria-label="Collapse versions">
          <PanelRightClose className="size-4" />
        </Button>
      </div>
      {/* A plain div, not the Radix ScrollArea used elsewhere - Radix's
          Viewport measures its content in a way that lets a wide,
          non-wrapping child (the transformation code block) escape its
          bounds instead of scrolling within them, silently widening this
          whole pane past the browser window. overflow-x-hidden alongside
          overflow-y-auto is load-bearing here too, same reasoning as the
          chat message list. */}
      <div className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
        <div className="min-w-0 p-3">
          {datasetId ? (
            <VersionTimeline datasetId={datasetId} userId={userId} />
          ) : (
            <EmptyState
              icon={History}
              title="No dataset resolved yet"
              description="Ask a question to see this dataset's version history here."
            />
          )}
        </div>
      </div>
    </div>
  );
}
