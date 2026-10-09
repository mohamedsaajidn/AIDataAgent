import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getDataset, listDatasets, uploadDataset } from "@/lib/api/datasets";

export function useDatasets(userId: string) {
  return useQuery({
    queryKey: ["datasets", userId],
    queryFn: () => listDatasets(userId),
    select: (data) => data.datasets,
    enabled: !!userId,
  });
}

export function useDataset(datasetId: string | null) {
  return useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => getDataset(datasetId as string),
    enabled: !!datasetId,
  });
}

export function useUploadDataset(userId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => uploadDataset(file, userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets", userId] });
    },
  });
}
