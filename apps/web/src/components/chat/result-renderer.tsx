import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { AskResult, DataframeResult } from "@/lib/types/api";

function isDataframeResult(value: unknown): value is DataframeResult {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as { type?: unknown }).type === "dataframe" &&
    Array.isArray((value as { columns?: unknown }).columns)
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isListOfObjects(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.length > 0 && value.every((r) => isPlainObject(r));
}

/** A dict whose values are themselves dicts - e.g. a per-column stats
 * summary keyed by column name (`{"age": {"Q1": 23, "Q3": 30}, ...}`, the
 * shape pandas' `.describe()`/groupby-agg results naturally take once
 * turned into a dict). Without this, these fell through to the generic
 * key/value branch below, which JSON.stringify's each nested dict - showing
 * the exact same data as an unreadable JSON blob right next to (or instead
 * of) a real table. */
function isDictOfDicts(value: unknown): value is Record<string, Record<string, unknown>> {
  if (!isPlainObject(value)) return false;
  const entries = Object.values(value);
  return entries.length > 0 && entries.every((v) => isPlainObject(v));
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Whether `ResultRenderer` will show `result` as an actual table (as
 * opposed to a scalar, bullet list, or key/value list). The message bubble
 * uses this to decide whether a markdown table in the LLM's own prose
 * answer would just be a duplicate of what's rendered here. */
export function resultRendersAsTable(result: AskResult): boolean {
  return isDataframeResult(result) || isListOfObjects(result) || isDictOfDicts(result);
}

function ScrollableTable({ children }: { children: React.ReactNode }) {
  return (
    <div className="max-w-full min-w-0 overflow-x-auto rounded-lg border border-border">
      <Table>{children}</Table>
    </div>
  );
}

/** Renders whatever agent.graphs.qa's `result` variable ended up being -
 * deliberately loose-typed on the backend (see lib/types/api.ts's
 * AskResult), so this has to branch on shape at runtime rather than trust
 * a schema. */
export function ResultRenderer({ result }: { result: AskResult }) {
  if (result === null || result === undefined || result === "") return null;

  if (isDataframeResult(result)) {
    return (
      <div className="max-w-full min-w-0 overflow-x-auto rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              {result.columns.map((c) => (
                <TableHead key={c}>{c}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {result.preview.map((row, i) => (
              <TableRow key={i}>
                {result.columns.map((c) => (
                  <TableCell key={c}>{formatCell(row[c])}</TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {typeof result.row_count === "number" && result.row_count > result.preview.length && (
          <p className="border-t border-border px-3 py-1.5 text-xs text-muted-foreground">
            Showing {result.preview.length} of {result.row_count} rows
          </p>
        )}
      </div>
    );
  }

  if (typeof result === "string" || typeof result === "number" || typeof result === "boolean") {
    // Deliberately not rendered here - for a scalar result, the backend's
    // own _format_result() already made this exact value the entire
    // `answer` text (see agent.graphs.qa), so the message bubble's main
    // text already shows it; a second copy here would just be noise.
    return null;
  }

  if (isDictOfDicts(result)) {
    const rowKeys = Object.keys(result);
    const columns = Array.from(new Set(Object.values(result).flatMap((r) => Object.keys(r))));
    return (
      <ScrollableTable>
        <TableHeader>
          <TableRow>
            <TableHead />
            {columns.map((c) => (
              <TableHead key={c}>{c}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rowKeys.map((rowKey) => (
            <TableRow key={rowKey}>
              <TableCell className="font-medium">{rowKey}</TableCell>
              {columns.map((c) => (
                <TableCell key={c}>{formatCell(result[rowKey][c])}</TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </ScrollableTable>
    );
  }

  if (Array.isArray(result)) {
    // A list of objects with a shared shape renders as a table; anything
    // else (scalars, mixed shapes) falls back to a bullet list.
    if (isListOfObjects(result)) {
      const columns = Array.from(new Set(result.flatMap((r) => Object.keys(r))));
      return (
        <ScrollableTable>
          <TableHeader>
            <TableRow>
              {columns.map((c) => (
                <TableHead key={c}>{c}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {result.map((row, i) => (
              <TableRow key={i}>
                {columns.map((c) => (
                  <TableCell key={c}>{formatCell(row[c])}</TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </ScrollableTable>
      );
    }
    return (
      <ul className="list-inside list-disc space-y-0.5 text-sm">
        {result.map((item, i) => (
          <li key={i}>{formatCell(item)}</li>
        ))}
      </ul>
    );
  }

  // Plain dict (not the dataframe shape, and not dict-of-dicts) - key/value list.
  return (
    <dl className="space-y-1 text-sm">
      {Object.entries(result as Record<string, unknown>).map(([k, v]) => (
        <div key={k} className="flex gap-2">
          <dt className="font-medium text-muted-foreground">{k}:</dt>
          <dd>{formatCell(v)}</dd>
        </div>
      ))}
    </dl>
  );
}
