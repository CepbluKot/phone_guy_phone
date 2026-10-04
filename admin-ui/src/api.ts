export type Profile = "original" | "phone-guy";
export type RouteSnapshot = {
  revision: number;
  extensions: Record<string, Profile>;
  browserExtensions?: Record<string, Profile>;
};
export type PhoneDevice = {
  mac: string;
  label: string;
  extension?: string;
  lastSeenIp?: string;
  lastSeenAt?: string;
  observation?: string;
};
export type PhonebookSnapshot = { revision: number; devices: PhoneDevice[] };
export type BrowserPhone = { nickname: string; extension: string; expiresAt: string };
export type AsteriskSnapshot = {
  ready: boolean;
  activeChannels: number;
  endpoints: { extension: string; state: "online" | "offline" | "unknown" }[];
};
export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
  ) {
    super(code);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...init, credentials: "same-origin" });
  if (!response.ok) {
    let code = "service_unavailable";
    try {
      code = (await response.json()).error ?? code;
    } catch {
      /* Keep the bounded fallback. */
    }
    throw new ApiError(response.status, code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  authMode: () => request<{ required: boolean }>("/admin/api/v1/auth-mode"),
  routes: () => request<RouteSnapshot>("/admin/api/v1/voice-routes"),
  phones: () => request<PhonebookSnapshot>("/admin/api/v1/phones"),
  asterisk: () => request<AsteriskSnapshot>("/admin/api/v1/asterisk"),
  browserPhones: () => request<{ sessions: BrowserPhone[] }>("/admin/api/v1/browser-phones"),
  login: (password: string) =>
    request<{ csrfToken: string }>("/admin/api/v1/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }),
  logout: (csrfToken: string) =>
    request<void>("/admin/api/v1/session", {
      method: "DELETE",
      headers: { "X-CSRF-Token": csrfToken },
    }),
  update: (
    extension: string,
    profile: Profile,
    revision: number,
    csrfToken: string,
  ) =>
    request<RouteSnapshot>(
      `/admin/api/v1/voice-routes/${encodeURIComponent(extension)}`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ profile, revision }),
      },
  ),
  updateBrowser: (extension: string, profile: Profile, revision: number, csrfToken: string) =>
    request<RouteSnapshot>(`/admin/api/v1/voice-routes/${encodeURIComponent(extension)}/browser`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken },
      body: JSON.stringify({ profile, revision }),
    }),
  updatePhone: (
    mac: string,
    label: string,
    extension: string,
    revision: number,
    csrfToken: string,
  ) =>
    request<PhonebookSnapshot>(
      `/admin/api/v1/phones/${encodeURIComponent(mac)}`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ label, extension, revision }),
      },
    ),
};
