"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark, oneLight } from "react-syntax-highlighter/dist/esm/styles/prism";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";

export function CodeBlock({ code, language = "python" }: { code: string; language?: string }) {
  const { resolvedTheme } = useTheme();
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="group relative max-w-full min-w-0 overflow-hidden rounded-lg border border-border">
      <Button
        variant="ghost"
        size="icon"
        className="absolute top-2 right-2 z-10 size-7 opacity-0 transition-opacity group-hover:opacity-100"
        onClick={handleCopy}
        aria-label="Copy code"
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </Button>
      <SyntaxHighlighter
        language={language}
        style={resolvedTheme === "dark" ? oneDark : oneLight}
        // A long unwrapped line (or a wide generated-code line) needs its
        // own local scrollbar here - explicit, not left to the theme's
        // default pre style, so it never depends on which theme is active.
        customStyle={{ margin: 0, fontSize: "0.8rem", padding: "1rem", overflowX: "auto", maxWidth: "100%" }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}
