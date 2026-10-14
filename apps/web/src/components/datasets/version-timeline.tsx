"use client";

import { Download, History } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CollapsibleCode } from "@/components/common/collapsible-code";
import { EmptyState } from "@/components/common/empty-state";
import { useSetActiveVersion, useVersions } from "@/hooks/use-transforms";
import { exportUrl } from "@/lib/api/datasets";
import { formatDate } from "@/lib/utils";
import type { ExportFormat } from "@/lib/types/api";

export function VersionTimeline({ datasetId }: { datasetId: string }) {
  const { data, isLoading } = useVersions(datasetId);
  const setActiveVersionMutation = useSetActiveVersion(datasetId);

  async function handleMakeActive(versionId: string) {
    try {
      await setActiveVersionMutation.mutateAsync(versionId);
      toast.success("Active version updated.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (!data || data.versions.length === 0) {
    return <EmptyState icon={History} title="No versions yet" />;
  }

  return (
    <ul className="space-y-3">
      {data.versions.map((v) => {
        const isActive = v.id === data.active_version_id;
        return (
          <li key={v.id} className="space-y-2 rounded-lg border border-border p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">v{v.version_number}</span>
              {isActive && <Badge>active</Badge>}
              <Badge variant="secondary" className="font-normal">
                {v.source}
              </Badge>
              <span className="text-xs text-muted-foreground">{formatDate(v.created_at)}</span>
            </div>
            <p className="text-sm text-muted-foreground">
              {v.row_count.toLocaleString()} rows × {v.column_count} cols
            </p>
            {v.diff_summary && <p className="text-sm">{v.diff_summary}</p>}
            {v.generated_code && <CollapsibleCode code={v.generated_code} label="Show transformation code" />}
            <div className="flex flex-wrap items-center gap-2 pt-1">
              {!isActive && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={setActiveVersionMutation.isPending}
                  onClick={() => handleMakeActive(v.id)}
                >
                  Make active (undo/redo)
                </Button>
              )}
              {(["csv", "xlsx", "parquet"] as ExportFormat[]).map((fmt) => (
                <Button key={fmt} size="sm" variant="ghost" asChild>
                  <a href={exportUrl(v.dataset_id, fmt, v.id)}>
                    <Download className="size-3.5" />
                    {fmt.toUpperCase()}
                  </a>
                </Button>
              ))}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
