// The only place a base URL/origin is configured is next.config.ts's
// rewrites() - every call here is a relative /api/... path, proxied
// same-origin to the FastAPI backend exactly like the old vite.config.ts
// dev proxy did. Never hardcode localhost:8000 anywhere else.
const BASE = "/api";

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch {
    // body wasn't JSON (e.g. a plain-text 500) - fall through to the status line
  }
  return `${res.status} ${res.statusText}`;
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return res.json() as Promise<T>;
}

/** For the export endpoint, which returns a raw file, not JSON. */
export async function requestBlob(path: string): Promise<Blob> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return res.blob();
}

export function apiUrl(path: string): string {
  return `${BASE}${path}`;
}
