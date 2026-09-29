"use client";

import { ShieldAlert, Table2 } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { EmptyState } from "@/components/common/empty-state";
import { useDatasetSample } from "@/hooks/use-transforms";
import { formatCell } from "@/lib/utils";

/** A quick, LLM-free look at the actual rows before diving into schema
 * details or version history - GET /datasets/{id}/sample, always against
 * the dataset's active version. PII columns are already redacted
 * server-side (see api.services.transforms.get_dataset_sample) - this only
 * has to show the caller which ones were dropped, never hide anything on
 * its own. */
export function DataSampleTable({ datasetId }: { datasetId: string }) {
  const { data, isLoading, error } = useDatasetSample(datasetId, 30);

  if (isLoading) {
    return (
      <div className="space-y-2">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <EmptyState
        icon={Table2}
        title="Couldn't load a sample"
        description={error instanceof Error ? error.message : "Something went wrong."}
      />
    );
  }

  if (data.rows.length === 0) {
    return <EmptyState icon={Table2} title="This dataset has no rows yet" />;
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <p>
          Showing {data.sample_row_count.toLocaleString()} of {data.row_count.toLocaleString()} rows (v
          {data.version_number})
        </p>
        {data.pii_columns.length > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <p className="flex items-center gap-1 text-amber-600">
                <ShieldAlert className="size-3.5" />
                {data.pii_columns.length} PII column{data.pii_columns.length > 1 ? "s" : ""} hidden
              </p>
            </TooltipTrigger>
            <TooltipContent>{data.pii_columns.join(", ")}</TooltipContent>
          </Tooltip>
        )}
      </div>
      <div className="max-w-full min-w-0 overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              {data.columns.map((c) => (
                <TableHead key={c} className="whitespace-nowrap">
                  {c}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.rows.map((row, i) => (
              <TableRow key={i}>
                {data.columns.map((c) => (
                  <TableCell key={c} className="whitespace-nowrap">
                    {formatCell(row[c])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
