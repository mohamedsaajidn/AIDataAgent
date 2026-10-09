"use client";

import { ChevronRight } from "lucide-react";

import { CodeBlock } from "@/components/common/code-block";

/** Native <details>/<summary> - matches the old app's exact "Show generated
 * code" collapsed-by-default UX, no extra JS state needed. */
export function CollapsibleCode({ code, label = "Show generated code" }: { code: string; label?: string }) {
  return (
    <details className="group/code">
      <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-muted-foreground select-none hover:text-foreground">
        <ChevronRight className="size-3.5 transition-transform group-open/code:rotate-90" />
        {label}
      </summary>
      <div className="mt-2">
        <CodeBlock code={code} />
      </div>
    </details>
  );
}
