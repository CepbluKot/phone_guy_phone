export type DirectoryEntry = { nickname: string; extension: string; active: boolean; physicalPhone?: string; physicalStatus?: "online" | "offline" | "unknown" };
export type PhoneSession = { sessionId: string; nickname: string; extension: string; expiresAt: string };
export type SIPCredentials = { uri: string; username: string; password: string; endpoint: string };
export type PhoneConfig = { signalingUrl: string; sipDomain?: string; iceServers?: RTCIceServer[] };

async function request<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(path, {
    method: body ? "POST" : "GET",
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!response.ok) {
    const code = (await response.json().catch(() => ({}))).error ?? "phone_unavailable";
    throw new Error(code);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const phoneAPI = {
  config: () => request<PhoneConfig>("/phone/api/v1/config"),
  directory: () => request<{ people: DirectoryEntry[] }>("/phone/api/v1/directory"),
  claim: (nickname: string, extension: string, createExtension = false) =>
    request<{ session: PhoneSession; sip: SIPCredentials }>("/phone/api/v1/claim", { nickname, extension, createExtension }),
  heartbeat: (sessionId: string) => request<PhoneSession>("/phone/api/v1/heartbeat", { sessionId }),
  hangup: (sessionId: string) => request<void>("/phone/api/v1/hangup", { sessionId }),
  release: (sessionId: string, keepalive = false) => {
    if (!keepalive) return request<void>("/phone/api/v1/release", { sessionId });
    return fetch("/phone/api/v1/release", {
      method: "POST",
      keepalive: true,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionId }),
    }).then(() => undefined);
  },
};
