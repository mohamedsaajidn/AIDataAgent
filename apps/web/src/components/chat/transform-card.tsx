"use client";

import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useCurrentUserRole } from "@/hooks/use-current-user";
import { useConfirmTransform, useRejectTransform } from "@/hooks/use-transforms";
import type { DiffSummary, PendingTransformStatus } from "@/lib/types/api";

/** Mirrors the backend's actual RBAC gate exactly - see
 * apps/api/src/api/services/users.require_editor and CLAUDE.md's
 * "Guardrails" section: confirm requires the editor role, reject is
 * deliberately left open to any role since it only discards a preview. */
export function TransformCard({
  pendingId,
  diff,
  userId,
  initialStatus,
}: {
  pendingId: string;
  diff: DiffSummary;
  userId: string;
  /** From session history (see ChatMessageRecord.pending_transform_status) -
   * undefined for a message just appended live this session, which can
   * only be "pending" so far. Without this, reopening a session with a
   * still-pending transform (or one already resolved elsewhere) rendered
   * the Confirm/Reject controls as if nothing had happened yet, or showed
   * live-resolvable controls for something already settled. */
  initialStatus?: PendingTransformStatus | null;
}) {
  const [resolved, setResolved] = useState<"confirmed" | "rejected" | null>(
    initialStatus === "confirmed" || initialStatus === "rejected" ? initialStatus : null,
  );
  const { data: whoami } = useCurrentUserRole(userId);
  const confirmMutation = useConfirmTransform();
  const rejectMutation = useRejectTransform();

  const canConfirm = whoami?.role === "editor";
  const resolving = confirmMutation.isPending || rejectMutation.isPending;

  async function handleConfirm() {
    try {
      await confirmMutation.mutateAsync({ pendingId, userId });
      setResolved("confirmed");
      toast.success("Transform applied - a new version was created.");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleReject() {
    try {
      await rejectMutation.mutateAsync(pendingId);
      setResolved("rejected");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-border bg-muted/30 p-4">
      <p className="text-sm">{diff.summary}</p>
      <p className="text-xs text-muted-foreground">
        columns: {diff.column_count_before} → {diff.column_count_after} · rows: {diff.row_count_before} →{" "}
        {diff.row_count_after}
      </p>

      {resolved ? (
        <p className={`text-sm font-medium ${resolved === "confirmed" ? "text-emerald-600" : "text-destructive"}`}>
          {resolved === "confirmed" ? "✓ Applied" : "✗ Discarded"}
        </p>
      ) : (
        <div className="flex gap-2 pt-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button size="sm" disabled={resolving || !canConfirm} onClick={handleConfirm}>
                  Confirm
                </Button>
              </span>
            </TooltipTrigger>
            {!canConfirm && <TooltipContent>Requires the editor role to commit changes.</TooltipContent>}
          </Tooltip>
          <Button size="sm" variant="outline" disabled={resolving} onClick={handleReject}>
            Reject
          </Button>
        </div>
      )}
    </div>
  );
}
