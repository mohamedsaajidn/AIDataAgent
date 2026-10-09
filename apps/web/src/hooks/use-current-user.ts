import { useQuery } from "@tanstack/react-query";

import { getMe } from "@/lib/api/users";

/** Feeds role-gated UI (transform-card's Confirm button, version-timeline's
 * Make-active button) so a viewer sees a disabled control with an
 * explanation instead of only finding out via a 403 toast after clicking -
 * see CLAUDE.md's "Guardrails" section for the actual server-side gate
 * this mirrors. */
export function useCurrentUserRole(userId: string) {
  return useQuery({
    queryKey: ["whoami", userId],
    queryFn: () => getMe(userId),
    enabled: !!userId,
    staleTime: 5 * 60 * 1000,
  });
}
