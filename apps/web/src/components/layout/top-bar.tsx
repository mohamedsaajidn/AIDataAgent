"use client";

import { ThemeToggle } from "@/components/layout/theme-toggle";

export function TopBar({ userId }: { userId: string }) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border px-4">
      <div />
      <div className="flex min-w-0 items-center gap-3">
        {/* The identity string can be a long email - truncate rather than
            let it force the header (and everything below it) wider on a
            narrow viewport, and drop it entirely below sm where there's no
            room for it next to the theme toggle. */}
        <span className="hidden max-w-40 truncate text-sm text-muted-foreground sm:inline">{userId}</span>
        <ThemeToggle />
      </div>
    </header>
  );
}
