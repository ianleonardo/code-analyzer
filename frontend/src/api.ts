export class ApiError extends Error {
  status: number;
  body: Record<string, unknown>;

  constructor(status: number, message: string, body: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export function authHeaders(): Headers {
  const headers = new Headers();
  const token = localStorage.getItem("ca-token");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return headers;
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = authHeaders();
  new Headers(options.headers).forEach((value, key) => headers.set(key, value));
  if (options.body && !(options.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = response.statusText;
    let body: Record<string, unknown> = {};
    try {
      body = (await response.json()) as Record<string, unknown>;
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      /* not JSON */
    }
    // Only an expired or invalid session signs the user out; other 401s (a Git host or directory rejecting a password) are ordinary errors.
    if (response.status === 401 && detail === "Not authenticated" && !path.endsWith("/auth/login")) {
      localStorage.removeItem("ca-token");
      if (!location.pathname.startsWith("/login")) location.assign("/login");
    }
    throw new ApiError(response.status, detail, body);
  }
  if (response.status === 204) return undefined as T;
  const type = response.headers.get("content-type") ?? "";
  if (!type.includes("json")) return (await response.text()) as T;
  return (await response.json()) as T;
}

export const post = <T,>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T,>(path: string, body?: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body ?? {}) });
export const patch = <T,>(path: string, body?: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body ?? {}) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });

export function errorText(err: unknown, fallback = "Something went wrong") {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : fallback;
}

/** Downloads a protected file with the login token. */
export async function downloadFile(path: string, filename: string) {
  const response = await fetch(path, { headers: authHeaders() });
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  const href = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(href);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function toDate(value: string) {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  return new Date(/^\d{4}-\d{2}-\d{2}[T ]/.test(value) && !hasZone ? `${value.replace(" ", "T")}Z` : value);
}

/** DD-MMM YYYY, e.g. 13-Oct 2026. The API sends naive UTC. */
export function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${String(date.getDate()).padStart(2, "0")}-${MONTHS[date.getMonth()]} ${date.getFullYear()}`;
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return "—";
  const date = toDate(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getDate())}-${MONTHS[date.getMonth()]} ${date.getFullYear()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function timeAgo(value: string | null | undefined) {
  if (!value) return "—";
  const seconds = Math.max(0, (Date.now() - toDate(value).getTime()) / 1000);
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function formatNumber(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(value);
}

export function compact(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 10_000) return `${Math.round(value / 1000)}k`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(value);
}
