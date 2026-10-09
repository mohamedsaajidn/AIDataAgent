"use client";

import { Database, MessageSquarePlus, PanelLeftClose, PanelLeftOpen, Sparkles } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn, formatDate } from "@/lib/utils";
import { useDatasets } from "@/hooks/use-datasets";
import { useSessions } from "@/hooks/use-chat";
import { useChatStore } from "@/store/chat-store";

// "Chat" used to also be a nav link here, pointing at the same /chat route
// the "New chat" button navigates to - but unlike the button, it never
// called startNewChat(), so it was a second way to reach the same screen
// without resetting anything: pure redundancy, not a distinct destination.
// Data Explorer is the only other top-level section.
const NAV_ITEMS = [{ href: "/datasets", label: "Data Explorer", icon: Database }];

const COLLAPSED_KEY = "sidebar-collapsed";
// Below this width there usually isn't room for both the chat column and a
// wide, always-open left sidebar without cramping the actual content - a
// narrower screen starts collapsed by default (the user can still reopen
// it), while an explicit prior choice (localStorage) always wins over this.
const AUTO_COLLAPSE_BREAKPOINT_PX = 1024;

export function Sidebar({ userId }: { userId: string }) {
  const pathname = usePathname();
  const router = useRouter();
  const startNewChat = useChatStore((s) => s.startNewChat);
  const { data: sessions, isLoading } = useSessions(userId);
  const { data: datasets } = useDatasets(userId);
  const [collapsed, setCollapsed] = useState(false);

  // Reading localStorage/matchMedia has to happen after mount to avoid an
  // SSR/client hydration mismatch (same technique as theme-toggle.tsx) - an
  // explicit prior choice always wins; only with no stored choice yet does
  // the current viewport width decide the default.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(COLLAPSED_KEY);
      if (stored !== null) {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time hydration-mismatch fix, see comment above
        setCollapsed(stored === "1");
        return;
      }
    } catch {
      // localStorage can throw (private browsing, blocked storage) - fall
      // through to the viewport-based default in that case.
    }
    setCollapsed(window.innerWidth < AUTO_COLLAPSE_BREAKPOINT_PX);
  }, []);

  function toggle() {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // Best-effort persistence only - a failed write just means this
        // choice won't be remembered next visit, nothing else breaks.
      }
      return next;
    });
  }

  function handleNewChat() {
    startNewChat();
    router.push("/chat");
  }

  if (collapsed) {
    return (
      <aside className="flex h-full w-14 shrink-0 flex-col items-center gap-1 border-r border-border bg-sidebar pt-4 text-sidebar-foreground">
        <Sparkles className="size-5 text-primary" />
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className="mt-3" onClick={toggle} aria-label="Expand sidebar">
              <PanelLeftOpen className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">Expand sidebar</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" onClick={handleNewChat} aria-label="New chat">
              <MessageSquarePlus className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">New chat</TooltipContent>
        </Tooltip>
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Tooltip key={item.href}>
              <TooltipTrigger asChild>
                <Link
                  href={item.href}
                  className={cn(
                    "flex size-8 items-center justify-center rounded-md",
                    active
                      ? "bg-sidebar-accent text-sidebar-accent-foreground"
                      : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
                  )}
                >
                  <item.icon className="size-4" />
                </Link>
              </TooltipTrigger>
              <TooltipContent side="right">{item.label}</TooltipContent>
            </Tooltip>
          );
        })}
      </aside>
    );
  }

  const datasetName = (id: string) => datasets?.find((d) => d.id === id)?.name ?? id;

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground">
      <div className="flex items-center justify-between px-4 py-4">
        <div className="flex items-center gap-2">
          <Sparkles className="size-5 text-primary" />
          <span className="text-sm font-semibold tracking-tight">DataAgent</span>
        </div>
        <Button variant="ghost" size="icon-sm" onClick={toggle} aria-label="Collapse sidebar">
          <PanelLeftClose className="size-4" />
        </Button>
      </div>

      <div className="px-3">
        <Button className="w-full justify-start gap-2" onClick={handleNewChat}>
          <MessageSquarePlus className="size-4" />
          New chat
        </Button>
      </div>

      <nav className="mt-3 flex flex-col gap-1 px-3">
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
              )}
            >
              <item.icon className="size-4" />
              {item.label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-4 flex-1 overflow-hidden px-3">
        <p className="px-1 pb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Recent chats</p>
        <ScrollArea className="h-full pb-4">
          {isLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          ) : !sessions || sessions.length === 0 ? (
            <p className="px-1 text-sm text-muted-foreground">No past chats yet.</p>
          ) : (
            <ul className="space-y-1">
              {sessions.map((s) => {
                const active = pathname === `/chat/${s.session_id}`;
                return (
                  <li key={s.session_id}>
                    <Link
                      href={`/chat/${s.session_id}`}
                      className={cn(
                        "block rounded-md px-3 py-2 text-sm transition-colors",
                        active ? "bg-sidebar-accent text-sidebar-accent-foreground" : "hover:bg-sidebar-accent/60",
                      )}
                    >
                      <p className="truncate font-medium">{s.last_question ?? "(no messages)"}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {s.dataset_ids.map(datasetName).join(", ") || "(no datasets)"}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {s.message_count} messages · {formatDate(s.created_at)}
                      </p>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </ScrollArea>
      </div>
    </aside>
  );
}
