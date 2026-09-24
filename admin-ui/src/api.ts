export type Profile = "original" | "phone-guy";
export type RouteSnapshot = {
  revision: number;
  extensions: Record<string, Profile>;
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
  routes: () => request<RouteSnapshot>("/admin/api/v1/voice-routes"),
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
};
