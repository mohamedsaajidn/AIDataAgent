/**
 * No real auth in this POC - identity comes from a build-time env var,
 * same model as the old Vite app's VITE_USER_ID. This is the single place that reads it,
 * so swapping in real auth later means changing this one function.
 */
export function getCurrentUserId(): string | null {
  const value = process.env.NEXT_PUBLIC_USER_ID?.trim();
  return value ? value : null;
}
