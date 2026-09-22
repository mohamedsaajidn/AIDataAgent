// No real auth in this POC - identity comes from a `.env.local` file
// (VITE_USER_ID), not a login flow. This is the single place that reads it,
// so swapping in real auth later means changing this one function, not
// every component that currently reads `useCurrentUserId()`.
export function getCurrentUserId(): string | null {
  const id = import.meta.env.VITE_USER_ID
  return id && id.trim() ? id.trim() : null
}
