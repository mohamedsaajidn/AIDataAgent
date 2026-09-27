"use client";

import { ArrowUp, Paperclip } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useUploadDataset } from "@/hooks/use-datasets";

export function ChatInput({
  userId,
  disabled,
  sendDisabled,
  onSend,
  onUpload,
}: {
  userId: string;
  /** A request is in flight - blocks sending AND uploading. */
  disabled: boolean;
  /** Blocks sending only (e.g. no dataset selected yet) - uploading should
   * still work, since attaching a file is often how you'd get a dataset
   * selected in the first place. Defaults to `disabled`. */
  sendDisabled?: boolean;
  onSend: (question: string) => void;
  /** Ids of every dataset created by the upload (a multi-sheet Excel file
   * can produce more than one) - the caller decides what "attach it" means
   * for its screen (auto-select on the new-chat landing, queue for the
   * next message on an active session). */
  onUpload?: (datasetIds: string[]) => void;
}) {
  const [value, setValue] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadDataset(userId);
  const blockSend = sendDisabled ?? disabled;

  function handleSend() {
    const trimmed = value.trim();
    if (!trimmed || blockSend) return;
    onSend(trimmed);
    setValue("");
  }

  async function handleFiles(files: FileList) {
    const newIds: string[] = [];
    for (const file of Array.from(files)) {
      try {
        const res = await uploadMutation.mutateAsync(file);
        newIds.push(...res.datasets.map((d) => d.id));
      } catch (err) {
        toast.error(`Failed to upload ${file.name}: ${err instanceof Error ? err.message : String(err)}`);
      }
    }
    if (newIds.length) onUpload?.(newIds);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  return (
    <div className="mx-auto w-full max-w-3xl p-4">
      <input
        ref={fileInputRef}
        type="file"
        accept=".csv,.xlsx"
        multiple
        className="hidden"
        onChange={(e) => {
          if (e.target.files?.length) void handleFiles(e.target.files);
        }}
      />
      {/* A single pill-shaped container (matching Claude/Gemini/ChatGPT's
          input) rather than a boxed textarea sitting beside two separate,
          differently-sized buttons - the textarea grows with content up to
          max-h-40 then scrolls internally, and the attach/send controls sit
          inside the same rounded shell as a compact bottom row. */}
      <div className="flex flex-col gap-1 rounded-3xl border border-input bg-background px-3 py-2 shadow-sm transition-colors focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Ask a question, request a transform/join/chart/EDA, or correct something…"
          disabled={disabled}
          rows={1}
          className="max-h-40 min-h-[28px] resize-none overflow-y-auto border-0 bg-transparent px-1 py-1 shadow-none focus-visible:border-transparent focus-visible:ring-0 dark:bg-transparent"
        />
        <div className="flex items-center justify-between">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="icon-sm"
                variant="ghost"
                className="rounded-full text-muted-foreground"
                disabled={disabled || uploadMutation.isPending}
                onClick={() => fileInputRef.current?.click()}
                aria-label="Attach a dataset"
              >
                <Paperclip className="size-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Upload a CSV or XLSX</TooltipContent>
          </Tooltip>
          <Button
            size="icon-sm"
            className="rounded-full"
            disabled={blockSend || !value.trim()}
            onClick={handleSend}
            aria-label="Send"
          >
            <ArrowUp className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
