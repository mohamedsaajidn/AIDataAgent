export function AccessDenied() {
  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-6">
      <div className="max-w-md space-y-4 text-center">
        <h1 className="text-2xl font-semibold text-foreground">Access denied</h1>
        <p className="text-muted-foreground">No user is configured for this app.</p>
        <p className="text-muted-foreground">
          Create <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-sm">apps/web/.env.local</code> with
          your identity, then restart the dev server:
        </p>
        <pre className="rounded-lg border border-border bg-muted px-4 py-3 text-left font-mono text-sm">
          NEXT_PUBLIC_USER_ID=you@example.com
        </pre>
      </div>
    </div>
  );
}
