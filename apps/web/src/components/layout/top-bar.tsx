"use client";

import { ThemeToggle } from "@/components/layout/theme-toggle";
import { RoleBadge } from "@/components/common/role-badge";
import { useCurrentUserRole } from "@/hooks/use-current-user";

export function TopBar({ userId }: { userId: string }) {
  const { data: whoami } = useCurrentUserRole(userId);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border px-4">
      <div />
      <div className="flex min-w-0 items-center gap-3">
        <RoleBadge role={whoami?.role} />
        {/* The identity string can be a long email - truncate rather than
            let it force the header (and everything below it) wider on a
            narrow viewport, and drop it entirely below sm where there's no
            room for it next to the role badge and theme toggle. */}
        <span className="hidden max-w-40 truncate text-sm text-muted-foreground sm:inline">{userId}</span>
        <ThemeToggle />
      </div>
    </header>
  );
}
