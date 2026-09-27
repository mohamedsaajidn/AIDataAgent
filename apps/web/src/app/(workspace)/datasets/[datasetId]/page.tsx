"use client";

import { MessagesSquare } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { SchemaTable } from "@/components/datasets/schema-table";
import { VersionTimeline } from "@/components/datasets/version-timeline";
import { useDataset } from "@/hooks/use-datasets";
import { getCurrentUserId } from "@/lib/auth";
import { formatNumber } from "@/lib/utils";
import { useChatStore } from "@/store/chat-store";

export default function DatasetDetailPage() {
  const userId = getCurrentUserId()!;
  const { datasetId } = useParams<{ datasetId: string }>();
  const router = useRouter();
  const { data: dataset, isLoading } = useDataset(datasetId);
  const startNewChat = useChatStore((s) => s.startNewChat);
  const toggleSelectedDataset = useChatStore((s) => s.toggleSelectedDataset);

  function handleAskAbout() {
    startNewChat();
    toggleSelectedDataset(datasetId);
    router.push("/chat");
  }

  if (isLoading || !dataset) {
    return (
      <div className="mx-auto max-w-5xl space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  return (
    <div className="mx-auto h-full max-w-5xl space-y-6 overflow-y-auto p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">{dataset.name}</h1>
          <p className="text-sm text-muted-foreground">
            {dataset.original_filename} · {formatNumber(dataset.row_count)} rows × {formatNumber(dataset.column_count)}{" "}
            cols
          </p>
        </div>
        <Button onClick={handleAskAbout} className="gap-2">
          <MessagesSquare className="size-4" />
          Ask about this dataset
        </Button>
      </div>

      <Tabs defaultValue="schema">
        <TabsList>
          <TabsTrigger value="schema">Schema</TabsTrigger>
          <TabsTrigger value="versions">Versions</TabsTrigger>
        </TabsList>
        <TabsContent value="schema" className="pt-4">
          <SchemaTable columns={dataset.columns} />
        </TabsContent>
        <TabsContent value="versions" className="pt-4">
          <VersionTimeline datasetId={datasetId} userId={userId} />
        </TabsContent>
      </Tabs>

      <p className="text-xs text-muted-foreground">
        Back to <Link href="/datasets" className="underline">Data Explorer</Link>
      </p>
    </div>
  );
}
