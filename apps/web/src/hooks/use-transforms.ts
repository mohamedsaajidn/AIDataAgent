import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { confirmTransform, listVersions, rejectTransform, setActiveVersion } from "@/lib/api/transforms";

export function useVersions(datasetId: string | null) {
  return useQuery({
    queryKey: ["versions", datasetId],
    queryFn: () => listVersions(datasetId as string),
    enabled: !!datasetId,
  });
}

export function useConfirmTransform() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: { pendingId: string; userId: string }) =>
      confirmTransform(params.pendingId, params.userId),
    onSuccess: () => {
      // The dataset this transform targeted isn't known here (a chat
      // message's TransformCard has no reliable dataset id - see
      // ui.ts's DisplayMessage), so invalidate every "versions" query by
      // key prefix rather than a specific dataset id - cheap, and it's
      // the only way a version sidebar left open on the affected dataset
      // reliably picks up the new version right after confirming.
      queryClient.invalidateQueries({ queryKey: ["versions"] });
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
    mutationFn: (params: { versionId: string; userId: string }) =>
      setActiveVersion(datasetId, params.versionId, params.userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["versions", datasetId] });
      queryClient.invalidateQueries({ queryKey: ["dataset", datasetId] });
    },
  });
}
