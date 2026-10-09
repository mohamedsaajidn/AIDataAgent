"use client";

import { Database } from "lucide-react";

import { DatasetCard } from "@/components/datasets/dataset-card";
import { UploadDropzone } from "@/components/datasets/upload-dropzone";
import { EmptyState } from "@/components/common/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useDatasets } from "@/hooks/use-datasets";
import { getCurrentUserId } from "@/lib/auth";

export default function DatasetsPage() {
  const userId = getCurrentUserId()!;
  const { data: datasets, isLoading } = useDatasets(userId);

  return (
    <div className="mx-auto h-full max-w-5xl space-y-6 overflow-y-auto p-6">
      <div>
        <h1 className="text-xl font-semibold">Data Explorer</h1>
        <p className="text-sm text-muted-foreground">Only datasets you&apos;ve uploaded are shown here.</p>
      </div>

      <UploadDropzone userId={userId} />

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : !datasets || datasets.length === 0 ? (
        <EmptyState icon={Database} title="No datasets uploaded yet" />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {datasets.map((d) => (
            <DatasetCard key={d.id} dataset={d} />
          ))}
        </div>
      )}
    </div>
  );
}
