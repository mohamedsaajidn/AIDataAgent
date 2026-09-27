import { Badge } from "@/components/ui/badge";
import type { ChatRoute } from "@/lib/types/api";

const LABELS: Record<string, string> = {
  sql: "SQL",
  pandas: "Pandas",
  chart: "Chart",
  chart_freehand: "Chart (freehand)",
  eda: "EDA",
  transform: "Transform",
  join: "Join",
  clarify: "Clarify",
  chat: "Chat",
};

export function RouteBadge({ route }: { route: ChatRoute | null | undefined }) {
  if (!route) return null;
  return (
    <Badge variant="secondary" className="font-normal">
      {LABELS[route] ?? route}
    </Badge>
  );
}
