import { ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatPercent } from "@/lib/utils";
import type { ColumnProfile } from "@/lib/types/api";

export function SchemaTable({ columns }: { columns: ColumnProfile[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Column</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Null %</TableHead>
            <TableHead>Distinct</TableHead>
            <TableHead>Sample values</TableHead>
            <TableHead>Description</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {columns.map((c) => (
            <TableRow key={c.name}>
              <TableCell className="font-medium whitespace-nowrap">
                <div className="flex items-center gap-1.5">
                  {c.name}
                  {c.is_pii && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <ShieldAlert className="size-3.5 shrink-0 text-amber-500" />
                      </TooltipTrigger>
                      <TooltipContent>PII: {c.pii_entities?.join(", ") || "flagged"}</TooltipContent>
                    </Tooltip>
                  )}
                </div>
              </TableCell>
              <TableCell>
                <Badge variant="secondary" className="font-normal">
                  {c.inferred_type}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">{formatPercent(c.null_pct)}</TableCell>
              <TableCell className="text-muted-foreground">{c.distinct_count.toLocaleString()}</TableCell>
              <TableCell className="max-w-[240px] truncate text-muted-foreground">
                {c.is_pii ? (
                  <span className="italic">hidden (PII)</span>
                ) : (
                  c.sample_values.map((v) => String(v)).join(", ")
                )}
              </TableCell>
              <TableCell className="max-w-[280px] truncate text-muted-foreground">{c.description ?? "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
