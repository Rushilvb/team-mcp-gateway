// The only place that talks to the gateway. Every page calls these functions.
// Requests are same-origin (/api on the same host as the dashboard), so the
// browser attaches the httpOnly session cookie automatically.

export type Role = "admin" | "member";

export interface Me { id: number; email: string; role: Role }

export interface User {
  id: number; email: string; role: Role;
  status: "active" | "invited" | "disabled";
  created_at: string; active_keys: number;
}

export interface ApiKey {
  id: number; user_id: number; name: string; prefix: string;
  created_at: string; last_used_at: string | null; revoked_at: string | null;
}

export type AuthType = "none" | "bearer" | "header";

export interface Server {
  slug: string; name: string; url: string; auth_type: AuthType; header_name: string | null;
  credential_set: boolean; enabled: boolean;
  status: "unknown" | "connected" | "unreachable" | "disabled";
  last_error: string | null; last_checked_at: string | null;
  tools: { name: string; description: string | null }[];
}

export interface ServerInput {
  slug?: string; name?: string; url?: string; auth_type?: AuthType;
  header_name?: string | null; credential?: string | null; enabled?: boolean;
}

export interface AuditItem {
  id: number; user_id: number | null; user_email: string | null; channel: "api" | "chat";
  server_slug: string | null; tool: string; started_at: string; duration_ms: number;
  outcome: "success" | "error" | "timeout" | "denied"; error_message: string | null;
}

export interface AuditPage { items: AuditItem[]; total: number; limit: number; offset: number }

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// FastAPI errors are {"detail": "message"} or, for validation errors,
// {"detail": [{"loc": [...], "msg": "..."}]}.
function messageOf(data: unknown): string | null {
  const detail = (data as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d: { loc?: unknown[]; msg?: string }) =>
      `${(d.loc ?? []).filter((p) => p !== "body").join(".")}: ${d.msg}`).join("; ");
  }
  return null;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, messageOf(data) ?? `${res.status} ${res.statusText}`);
  return data as T;
}

export const api = {
  // auth
  login: (email: string, password: string) => request<Me>("POST", "/auth/login", { email, password }),
  logout: () => request<void>("POST", "/auth/logout"),
  me: () => request<Me>("GET", "/auth/me"),
  inviteInfo: (token: string) => request<{ email: string }>("GET", `/invites/${encodeURIComponent(token)}`),
  acceptInvite: (token: string, password: string) =>
    request<Me>("POST", `/invites/${encodeURIComponent(token)}/accept`, { password }),

  // members (admin)
  users: () => request<User[]>("GET", "/users"),
  inviteUser: (email: string, role: Role) =>
    request<{ user: User; invite_url: string }>("POST", "/users", { email, role }),
  reissueInvite: (id: number) => request<{ user: User; invite_url: string }>("POST", `/users/${id}/invite`),
  updateUser: (id: number, change: { role?: Role; status?: "active" | "disabled" }) =>
    request<User>("PATCH", `/users/${id}`, change),
  userKeys: (id: number) => request<ApiKey[]>("GET", `/users/${id}/keys`),
  issueKey: (id: number, name: string) =>
    request<{ key: string; item: ApiKey }>("POST", `/users/${id}/keys`, { name }),

  // API keys
  myKeys: () => request<ApiKey[]>("GET", "/me/keys"),
  createKey: (name: string) => request<{ key: string; item: ApiKey }>("POST", "/me/keys", { name }),
  revokeKey: (id: number) => request<void>("DELETE", `/keys/${id}`),

  // upstream servers
  servers: () => request<Server[]>("GET", "/servers"),
  createServer: (input: ServerInput) => request<Server>("POST", "/servers", input),
  updateServer: (slug: string, input: ServerInput) => request<Server>("PATCH", `/servers/${slug}`, input),
  deleteServer: (slug: string) => request<void>("DELETE", `/servers/${slug}`),
  testServer: (slug: string) =>
    request<{ slug: string; status: string; last_error: string | null; tool_count: number }>("POST", `/servers/${slug}/test`),

  // audit
  audit: (params: Record<string, string | number | undefined>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
    return request<AuditPage>("GET", `/audit?${q}`);
  },
};

export function formatTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "never";
}
