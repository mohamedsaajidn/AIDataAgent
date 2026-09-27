export { cn } from "cn";

/** null_pct from the backend is a 0-1 fraction, never a percentage - see
 * lib/types/api.ts's ColumnProfile doc comment. This is the one place that
 * converts it for display. */
export function formatPercent(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

export function formatNumber(value: number | null): string {
  if (value === null) return "—";
  return value.toLocaleString();
}

/** Removes GFM pipe-table blocks from markdown text. Used when a message's
 * structured `result` already renders as a real, properly-scrollable table
 * (see result-renderer.tsx's resultRendersAsTable) - the LLM's own prose
 * answer sometimes restates that exact same data as a markdown table too,
 * which showed up as two tables stacked in the same bubble (one of them an
 * unstyled, unscrollable table that just overflowed the bubble). */
export function stripMarkdownTables(markdown: string): string {
  const lines = markdown.split("\n");
  const isSeparatorRow = (line: string) =>
    /^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/.test(line);

  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const headerLine = lines[i];
    const separatorLine = lines[i + 1];
    if (headerLine?.includes("|") && separatorLine !== undefined && isSeparatorRow(separatorLine)) {
      let j = i + 2;
      while (j < lines.length && lines[j].trim() !== "" && lines[j].includes("|")) j++;
      i = j;
      continue;
    }
    out.push(headerLine);
    i++;
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}
