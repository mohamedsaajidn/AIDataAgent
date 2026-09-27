"use client";

import { Database } from "lucide-react";

import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/common/empty-state";
import { useDatasets } from "@/hooks/use-datasets";
import { formatNumber } from "@/lib/utils";

export function DatasetPicker({
  userId,
  selectedIds,
  onToggle,
}: {
  userId: string;
  selectedIds: string[];
  onToggle: (id: string) => void;
}) {
  const { data: datasets, isLoading } = useDatasets(userId);

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }

  if (!datasets || datasets.length === 0) {
    return (
      <EmptyState
        icon={Database}
        title="No datasets yet"
        description="Upload a CSV or XLSX from the Data Explorer to start a chat."
      />
    );
  }

  return (
    <ul className="space-y-2">
      {datasets.map((d) => (
        <li key={d.id}>
          <label className="flex cursor-pointer items-center gap-3 rounded-lg border border-border p-3 hover:bg-muted/50">
            <Checkbox checked={selectedIds.includes(d.id)} onCheckedChange={() => onToggle(d.id)} />
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{d.name}</p>
              <p className="text-xs text-muted-foreground">
                {formatNumber(d.row_count)} rows × {formatNumber(d.column_count)} cols
              </p>
            </div>
          </label>
        </li>
      ))}
    </ul>
  );
}
