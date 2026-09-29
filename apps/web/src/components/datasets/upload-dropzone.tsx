"use client";

import { UploadCloud } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { useUploadDataset } from "@/hooks/use-datasets";

export function UploadDropzone({ userId }: { userId: string }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadDataset(userId);

  async function uploadFiles(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      // Updated in place to success/error via the same id - a loading toast
      // per file so a multi-file upload gives per-file confirmation, not
      // just the dropzone's single "Uploading…" label swap covering the
      // whole batch.
      const toastId = toast.loading(`Uploading ${file.name}…`);
      try {
        await uploadMutation.mutateAsync(file);
        toast.success(`${file.name} uploaded.`, { id: toastId });
      } catch (err) {
        toast.error(`Failed to upload ${file.name}: ${err instanceof Error ? err.message : String(err)}`, {
          id: toastId,
        });
      }
    }
    if (inputRef.current) inputRef.current.value = "";
  }

  return (
    <label
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition-colors",
        dragging ? "border-primary bg-accent" : "border-border hover:bg-muted/40",
      )}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (e.dataTransfer.files.length) void uploadFiles(e.dataTransfer.files);
      }}
    >
      <UploadCloud className="size-6 text-muted-foreground" />
      <p className="text-sm font-medium">
        {uploadMutation.isPending ? "Uploading…" : "Drag & drop, or click to upload"}
      </p>
      <p className="text-xs text-muted-foreground">CSV or XLSX, multiple files allowed</p>
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx"
        multiple
        className="hidden"
        disabled={uploadMutation.isPending}
        onChange={(e) => {
          if (e.target.files?.length) void uploadFiles(e.target.files);
        }}
      />
    </label>
  );
}
