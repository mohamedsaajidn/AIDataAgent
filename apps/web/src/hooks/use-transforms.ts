import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { confirmTransform, getDatasetSample, listVersions, rejectTransform, setActiveVersion } from "@/lib/api/transforms";

export function useVersions(datasetId: string | null) {
  return useQuery({
    queryKey: ["versions", datasetId],
    queryFn: () => listVersions(datasetId as string),
    enabled: !!datasetId,
  });
}

export function useDatasetSample(datasetId: string | null, limit = 30) {
  return useQuery({
    queryKey: ["sample", datasetId, limit],
    queryFn: () => getDatasetSample(datasetId as string, limit),
    enabled: !!datasetId,
  });
}

export function useConfirmTransform() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (pendingId: string) => confirmTransform(pendingId),
    onSuccess: () => {
      // The dataset this transform targeted isn't known here (a chat
      // message's TransformCard has no reliable dataset id - see
      // ui.ts's DisplayMessage), so invalidate every "versions"/"sample"
      // query by key prefix rather than a specific dataset id - cheap, and
      // it's the only way a version sidebar or sample tab left open on the
      // affected dataset reliably picks up the change right after
      // confirming.
      queryClient.invalidateQueries({ queryKey: ["versions"] });
      queryClient.invalidateQueries({ queryKey: ["sample"] });
    },
  });
}

export function useRejectTransform() {
  return useMutation({
    mutationFn: (pendingId: string) => rejectTransform(pendingId),
  });
}

export function useSetActiveVersion(datasetId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (versionId: string) => setActiveVersion(datasetId, versionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions", datasetId] });
      queryClient.invalidateQueries({ queryKey: ["dataset", datasetId] });
      queryClient.invalidateQueries({ queryKey: ["sample", datasetId] });
    },
  });
}
