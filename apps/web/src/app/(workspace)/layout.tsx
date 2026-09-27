import { AccessDenied } from "@/app/access-denied";
import { Sidebar } from "@/components/layout/sidebar";
import { TopBar } from "@/components/layout/top-bar";
import { getCurrentUserId } from "@/lib/auth";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const userId = getCurrentUserId();
  if (!userId) return <AccessDenied />;

  return (
    <div className="flex h-svh overflow-hidden bg-background">
      <Sidebar userId={userId} />
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <TopBar userId={userId} />
        <main className="flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}
