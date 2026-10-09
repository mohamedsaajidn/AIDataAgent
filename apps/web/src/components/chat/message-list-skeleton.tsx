import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";

/** Shown in place of the message list while a session's history is still
 * loading - mirrors the actual bubble layout (alternating user/assistant,
 * roughly bubble-shaped widths) instead of a flat "Loading conversation…"
 * line, so the transition into real content doesn't jump. */
export function MessageListSkeleton() {
  const rows = [
    { user: "w-56", assistant: ["w-80", "w-64"] },
    { user: "w-40", assistant: ["w-72"] },
    { user: "w-64", assistant: ["w-full", "w-48"] },
  ];

  return (
    <div className="mx-auto flex w-full min-w-0 max-w-3xl flex-col gap-4 px-4 py-6">
      {rows.map((row, i) => (
        <div key={i} className="flex min-w-0 flex-col gap-4">
          <div className="flex justify-end">
            <Skeleton className={cn("h-9 rounded-2xl", row.user)} />
          </div>
          <div className="flex flex-col gap-2">
            {row.assistant.map((w, j) => (
              <Skeleton key={j} className={cn("h-4 rounded-md", w)} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
