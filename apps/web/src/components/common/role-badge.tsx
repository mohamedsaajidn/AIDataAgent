import { ShieldCheck, Eye } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { UserRole } from "@/lib/types/api";

export function RoleBadge({ role }: { role: UserRole | undefined }) {
  if (!role) return null;

  if (role === "editor") {
    return (
      <Badge variant="outline" className="gap-1 border-primary/40 text-primary">
        <ShieldCheck className="size-3" />
        Editor
      </Badge>
    );
  }

  return (
    <Badge variant="outline" className="gap-1 text-muted-foreground">
      <Eye className="size-3" />
      Viewer
    </Badge>
  );
}
