"use client";

import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";

/** The backend is a single blocking request (no token streaming - see the
 * "Streaming scope" decision in the frontend rewrite plan), so this is an
 * honest "still working, here's how long" indicator rather than fake
 * stage labels that don't reflect what's actually happening. */
export function WorkingIndicator() {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - start) / 1000)), 500);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin text-primary" />
      <span>Agent is working{elapsed > 0 ? ` · ${elapsed}s` : "…"}</span>
    </div>
  );
}
