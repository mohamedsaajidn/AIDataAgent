"use client";

import { AlertTriangle } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ArtifactFrame } from "@/components/chat/artifact-frame";
import { ResultRenderer, resultRendersAsTable } from "@/components/chat/result-renderer";
import { TransformCard } from "@/components/chat/transform-card";
import { CollapsibleCode } from "@/components/common/collapsible-code";
import { RouteBadge } from "@/components/common/route-badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn, stripMarkdownTables } from "@/lib/utils";
import type { DisplayMessage } from "@/lib/types/ui";

// Any markdown table the LLM's prose still contains (once it isn't a
// duplicate of `result` - see stripMarkdownTables below) gets rendered with
// the same scrollable, styled table used everywhere else in the app,
// instead of react-markdown's bare <table>, which has no overflow handling
// and just overflows the bubble on a wide table.
const MARKDOWN_COMPONENTS = {
  table: Table,
  thead: TableHeader,
  tbody: TableBody,
  tr: TableRow,
  th: TableHead,
  td: TableCell,
};

export function MessageBubble({ message, userId }: { message: DisplayMessage; userId: string }) {
  const isUser = message.role === "user";
  const content =
    message.content && resultRendersAsTable(message.result ?? null)
      ? stripMarkdownTables(message.content)
      : message.content;

  return (
    <div className={cn("flex min-w-0", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          // min-w-0 lets this bubble actually shrink inside its flex parent
          // instead of growing to fit whatever's inside it (a long code
          // line, an unbroken URL) - without it, that content pushes the
          // whole chat column wider instead of wrapping/scrolling locally.
          "min-w-0 max-w-[85%] space-y-3 rounded-2xl px-4 py-3 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "border border-border bg-card",
        )}
      >
        {!isUser && message.route && (
          <div className="flex items-center gap-2">
            <RouteBadge route={message.route} />
          </div>
        )}

        {content && (
          <div
            className={cn(
              "prose prose-sm min-w-0 max-w-none break-words",
              isUser ? "prose-invert" : "dark:prose-invert",
              "prose-p:leading-relaxed prose-pre:bg-transparent prose-pre:p-0",
              "prose-table:my-0",
            )}
          >
            <Markdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
              {content}
            </Markdown>
          </div>
        )}

        {message.error && (
          <p className="flex items-center gap-1.5 text-sm text-destructive">
            <AlertTriangle className="size-3.5" />
            {message.error}
          </p>
        )}

        {message.result !== undefined && <ResultRenderer result={message.result} />}

        {message.code && <CollapsibleCode code={message.code} />}

        {message.artifactHtml && <ArtifactFrame html={message.artifactHtml} />}

        {message.pendingTransformId && message.diff && (
          <TransformCard
            pendingId={message.pendingTransformId}
            diff={message.diff}
            userId={userId}
            initialStatus={message.pendingTransformStatus}
          />
        )}
      </div>
    </div>
  );
}
