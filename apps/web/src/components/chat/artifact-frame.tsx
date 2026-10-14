"use client";

import { Maximize2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

/** Charts/EDA reports come back as self-contained HTML. Rendered in a sandboxed iframe so that HTML never runs
 * with access to this app's DOM/cookies/storage.
 *
 * Shown at a modest inline size that fits the chat column, with an expand
 * button that reopens the exact same srcDoc in a much larger dialog -
 * charts/EDA reports can be tall or wide (a multi-column EDA distribution
 * grid especially), and a small fixed-size frame left them cramped with
 * their own internal scrollbars either way; this keeps the inline view
 * compact while still giving a real, comfortably-sized look on demand. */
export function ArtifactFrame({ html }: { html: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <div className="group relative">
        <iframe
          sandbox="allow-scripts"
          srcDoc={html}
          title="artifact"
          className="h-[360px] w-full rounded-lg border border-border bg-white"
        />
        <Button
          variant="secondary"
          size="icon-sm"
          className="absolute top-2 right-2 opacity-0 shadow-sm transition-opacity group-hover:opacity-100"
          onClick={() => setExpanded(true)}
          aria-label="Expand"
        >
          <Maximize2 className="size-3.5" />
        </Button>
      </div>

      <Dialog open={expanded} onOpenChange={setExpanded}>
        <DialogContent className="flex h-[85vh] w-[95vw] max-w-6xl flex-col gap-2 sm:max-w-6xl">
          <DialogTitle className="sr-only">Expanded chart</DialogTitle>
          <iframe sandbox="allow-scripts" srcDoc={html} title="artifact (expanded)" className="min-h-0 flex-1 rounded-lg border border-border bg-white" />
        </DialogContent>
      </Dialog>
    </>
  );
}
