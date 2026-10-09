import { ShieldAlert, Table2 } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { formatDate, formatNumber } from "@/lib/utils";
import type { DatasetSummary } from "@/lib/types/api";

export function DatasetCard({ dataset }: { dataset: DatasetSummary }) {
  const piiCount = dataset.columns.filter((c) => c.is_pii).length;

  return (
    <Link href={`/datasets/${dataset.id}`}>
      <Card className="h-full transition-colors hover:border-primary/50">
        <CardHeader className="flex-row items-start justify-between gap-2 space-y-0">
          <div className="min-w-0">
            <p className="truncate font-medium">{dataset.name}</p>
            <p className="truncate text-xs text-muted-foreground">{dataset.original_filename}</p>
          </div>
          <Table2 className="size-4 shrink-0 text-muted-foreground" />
        </CardHeader>
        <CardContent className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {formatNumber(dataset.row_count)} rows × {formatNumber(dataset.column_count)} cols
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-normal">
              {formatDate(dataset.created_at)}
            </Badge>
            {piiCount > 0 && (
              <Badge variant="outline" className="gap-1 border-amber-500/40 font-normal text-amber-600">
                <ShieldAlert className="size-3" />
                {piiCount} PII column{piiCount > 1 ? "s" : ""}
              </Badge>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
