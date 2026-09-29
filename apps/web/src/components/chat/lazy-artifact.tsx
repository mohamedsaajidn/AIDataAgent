"use client";

import { AlertTriangle } from "lucide-react";

import { ArtifactFrame } from "@/components/chat/artifact-frame";
import { Skeleton } from "@/components/ui/skeleton";
import { useMessageArtifact } from "@/hooks/use-chat";

/** Fetches a message's chart/EDA artifact on demand instead of the caller
 * already having it inline - see AskResponse.has_artifact's doc comment for
 * why this is fetched separately (a multi-MB artifact inlined into every
 * message in every history load doesn't scale; confirmed live at 84MB for
 * one real chart-heavy session). */
export function LazyArtifact({ messageId, hasArtifact }: { messageId: string | undefined; hasArtifact: boolean | undefined }) {
  const { data: html, isLoading, error } = useMessageArtifact(messageId, hasArtifact);

  if (!hasArtifact) return null;

  if (isLoading) {
    return <Skeleton className="h-[360px] w-full rounded-lg" />;
  }

  if (error || !html) {
    return (
      <p className="flex items-center gap-1.5 text-sm text-destructive">
        <AlertTriangle className="size-3.5" />
        Couldn&apos;t load this chart{error instanceof Error ? `: ${error.message}` : ""}.
      </p>
    );
  }

  return <ArtifactFrame html={html} />;
}
