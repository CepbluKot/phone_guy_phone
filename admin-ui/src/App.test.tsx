import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

const routes = {
  revision: 4,
  extensions: { "1983": "original", "1987": "phone-guy" },
};
const phonebook = { revision: 1, devices: [] };
const metrics = {
  updatedAt: "2026-09-25T00:00:00Z",
  rvc: { status: "ready", active: false, running: false, queuedWindows: 0, fresh: true },
  calls: { active: 0, limit: 2 },
  processing: { samples: 0 },
  errors: [],
  host: { status: "ready", cpuReady: true, rvcCpuReady: true },
};
const response = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
const originalInnerWidth = Object.getOwnPropertyDescriptor(window, "innerWidth");

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.removeItem("voice-control-sidebar-collapsed");
  if (originalInnerWidth) Object.defineProperty(window, "innerWidth", originalInnerWidth);
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
});

describe("phone profile admin", () => {
  it("shows live SIP registration state on the Asterisk page", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/admin/api/v1/auth-mode") return response(200, { required: false });
      if (path === "/admin/api/v1/voice-routes") return response(200, routes);
      if (path === "/admin/api/v1/phones") return response(200, { revision: 2, devices: [
        { mac: "00:11:22:33:44:55", label: "Desk phone", extension: "1983" },
        { mac: "00:11:22:33:44:56", label: "Spare phone", extension: "1987" },
      ] });
      if (path === "/admin/api/v1/asterisk") return response(200, {
        ready: true,
        activeChannels: 1,
        endpoints: [
          { extension: "1983", state: "online" },
          { extension: "1987", state: "offline" },
        ],
      });
      if (path === "/admin/api/v1/browser-phones") return response(200, { sessions: [] });
      return response(200, metrics);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "Asterisk" }));
    expect(await screen.findByRole("heading", { name: "Статус Asterisk" })).toBeInTheDocument();
    expect(screen.getByText("Активные каналы")).toBeInTheDocument();
    expect(screen.getByText("В сети")).toBeInTheDocument();
    expect(screen.getByText("Не в сети")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/asterisk");
  });

  it("opens a mobile drawer and closes it after choosing a section", async () => {
    window.localStorage.removeItem("voice-control-sidebar-collapsed");
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(200, { required: false }))
      .mockResolvedValueOnce(response(200, routes))
      .mockResolvedValueOnce(response(200, phonebook)));
    render(<App />);

    const menu = await screen.findByRole("button", { name: "Открыть меню" });
    expect(menu).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(menu);
    expect(menu).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: "Закрыть панель навигации" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Профили голоса" }));
    expect(menu).toHaveAttribute("aria-expanded", "false");
    expect(await screen.findByRole("heading", { name: "Профили телефонов" })).toBeInTheDocument();
  });

  it("labels the mobile physical-phone card fields", async () => {
    window.localStorage.removeItem("voice-control-sidebar-collapsed");
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(response(200, { required: false }))
      .mockResolvedValueOnce(response(200, routes))
      .mockResolvedValueOnce(response(200, { revision: 2, devices: [{ mac: "00:0b:82:f4:f2:8b", label: "Grandstream desk phone", lastSeenIp: "192.168.20.66", lastSeenAt: "2026-09-29" }] }))
      );
    render(<App />);

    const deviceName = await screen.findByDisplayValue("Grandstream desk phone");
    expect(deviceName.closest("td")).toHaveAttribute("data-label", "Устройство");
    expect(screen.getByText("00:0b:82:f4:f2:8b").closest("td")).toHaveAttribute("data-label", "MAC-адрес");
    expect(screen.getByText("192.168.20.66").closest("td")).toHaveAttribute("data-label", "IP");
  });

  it("shows only known phones without an extension and lets me assign one", async () => {
    let inventory: { revision: number; devices: { mac: string; label: string; extension?: string; lastSeenIp?: string; lastSeenAt?: string }[] } = {
      revision: 1,
      devices: [
        { mac: "00:11:22:33:44:55", label: "Assigned desk phone", extension: "1983" },
        { mac: "00:11:22:33:44:66", label: "New desk phone", lastSeenIp: "192.168.20.99", lastSeenAt: "2026-10-01" },
      ],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/admin/api/v1/auth-mode") return response(200, { required: false });
      if (path === "/admin/api/v1/voice-routes") return response(200, routes);
      if (path.startsWith("/admin/api/v1/phones/") && init?.method === "PUT") {
        inventory = {
          revision: 2,
          devices: [
            { mac: "00:11:22:33:44:55", label: "Assigned desk phone", extension: "1983" },
            { mac: "00:11:22:33:44:66", label: "New desk phone", extension: "1987", lastSeenIp: "192.168.20.99" },
          ],
        };
        return response(200, inventory);
      }
      if (path === "/admin/api/v1/phones") return response(200, inventory);
      if (path === "/admin/api/v1/asterisk") return response(200, { ready: true, activeChannels: 0, endpoints: [] });
      if (path === "/admin/api/v1/browser-phones") return response(200, { sessions: [] });
      return response(200, metrics);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const unassigned = await screen.findByRole("region", { name: "Телефоны без номера" });
    expect(within(unassigned).getByDisplayValue("New desk phone")).toBeInTheDocument();
    expect(within(unassigned).queryByDisplayValue("Assigned desk phone")).not.toBeInTheDocument();
    expect(within(unassigned).getByText("Есть в истории")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Нагрузка системы" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Добавить телефон" })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalledWith("/admin/api/v1/metrics", expect.anything());

    await userEvent.selectOptions(within(unassigned).getByRole("combobox", { name: "Телефон 00:11:22:33:44:66" }), "1987");
    await userEvent.click(within(unassigned).getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(screen.getByText("Привязка сохранена")).toBeInTheDocument());
    const put = fetchMock.mock.calls.find(([path, init]) => String(path).includes("/admin/api/v1/phones/") && init?.method === "PUT");
    expect(put).toBeTruthy();
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ label: "New desk phone", extension: "1987", revision: 1 });
  });

  it("exposes the selected Voice section in an accessible application navigation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(response(200, { required: false }))
        .mockResolvedValueOnce(response(200, routes))
        .mockResolvedValueOnce(response(200, phonebook))
        .mockResolvedValue(response(200, metrics)),
    );
    render(<App />);

    const navigation = await screen.findByRole("navigation", { name: "Управление" });
    expect(navigation).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Телефоны" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("button", { name: "Свернуть меню" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Профили голоса" }));
    expect(screen.getByRole("button", { name: "Профили голоса" })).toHaveAttribute("aria-current", "page");
    expect(window.location.hash).toBe("#/profiles");
    expect(screen.getByRole("navigation", { name: "Breadcrumb" })).toHaveTextContent(/Админка\s*\/\s*Профили голоса/);
  });

  it("requires login, then loads and displays current phone profiles", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(200, { required: true }))
      .mockResolvedValueOnce(response(401, { error: "unauthorized" }))
      .mockResolvedValueOnce(response(201, { csrfToken: "csrf-one" }))
      .mockResolvedValueOnce(response(200, routes))
      .mockResolvedValueOnce(response(200, phonebook));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "EN" }));
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeTruthy();
    await userEvent.type(screen.getByLabelText("Admin password"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await userEvent.click(await screen.findByRole("button", { name: "Voice profiles" }));
    expect(
      await screen.findByRole("heading", { name: "Phone profiles" }),
    ).toBeTruthy();
    expect(screen.getByText("1983")).toBeTruthy();
    expect(screen.getByLabelText("Profile for 1987")).toHaveValue("phone-guy");
  });

  it("saves a selected profile with the currently loaded revision", async () => {
    const updated = {
      revision: 5,
      extensions: { ...routes.extensions, "1983": "phone-guy" },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(200, { required: true }))
      .mockResolvedValueOnce(response(401, { error: "unauthorized" }))
      .mockResolvedValueOnce(response(201, { csrfToken: "csrf-two" }))
      .mockResolvedValueOnce(response(200, routes))
      .mockResolvedValueOnce(response(200, phonebook))
      .mockResolvedValueOnce(response(200, { ready: true, activeChannels: 0, endpoints: [] }))
      .mockResolvedValueOnce(response(200, { sessions: [] }))
      .mockResolvedValueOnce(response(200, updated))
      .mockResolvedValue(response(200, metrics));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "EN" }));
    await userEvent.type(
      await screen.findByLabelText("Admin password"),
      "secret",
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await userEvent.click(await screen.findByRole("button", { name: "Voice profiles" }));
    const select = await screen.findByLabelText("Profile for 1983");
    await userEvent.selectOptions(select, "phone-guy");
    await userEvent.click(screen.getByRole("button", { name: "Save 1983" }));
    expect(await screen.findByText("Saved")).toBeTruthy();
    const put = fetchMock.mock.calls.find(
      ([path]) => path === "/admin/api/v1/voice-routes/1983",
    )!;
    expect(put[0]).toBe("/admin/api/v1/voice-routes/1983");
    expect(JSON.parse(String(put[1].body))).toEqual({
      profile: "phone-guy",
      revision: 4,
    });
    expect(new Headers(put[1].headers).get("X-CSRF-Token")).toBe("csrf-two");
    expect(put[1].credentials).toBe("same-origin");
  });

  it("refreshes routes after a stale revision conflict", async () => {
    const fresh = {
      revision: 8,
      extensions: { ...routes.extensions, "1983": "original" },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(200, { required: true }))
      .mockResolvedValueOnce(response(401, { error: "unauthorized" }))
      .mockResolvedValueOnce(response(201, { csrfToken: "csrf-three" }))
      .mockResolvedValueOnce(response(200, routes))
      .mockResolvedValueOnce(response(200, phonebook))
      .mockResolvedValueOnce(response(200, { ready: true, activeChannels: 0, endpoints: [] }))
      .mockResolvedValueOnce(response(200, { sessions: [] }))
      .mockResolvedValueOnce(response(409, { error: "stale_revision" }))
      .mockResolvedValueOnce(response(200, fresh))
      .mockResolvedValueOnce(response(200, phonebook))
      .mockResolvedValue(response(200, metrics));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "EN" }));
    await userEvent.type(
      await screen.findByLabelText("Admin password"),
      "secret",
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await userEvent.click(await screen.findByRole("button", { name: "Voice profiles" }));
    await userEvent.selectOptions(
      await screen.findByLabelText("Profile for 1983"),
      "phone-guy",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save 1983" }));
    expect(await screen.findByText(/changed in another session/i)).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByLabelText("Profile for 1983")).toHaveValue("original"),
    );
  });

  it("loads and updates profiles without a password when auth is disabled", async () => {
    const updated = {
      revision: 5,
      extensions: { ...routes.extensions, "1983": "phone-guy" },
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(200, { required: false }))
      .mockResolvedValueOnce(response(200, routes))
      .mockResolvedValueOnce(response(200, phonebook))
      .mockResolvedValueOnce(response(200, { ready: true, activeChannels: 0, endpoints: [] }))
      .mockResolvedValueOnce(response(200, { sessions: [] }))
      .mockResolvedValueOnce(response(200, updated))
      .mockResolvedValue(response(200, metrics));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Профили голоса" }));
    await userEvent.selectOptions(
      await screen.findByLabelText("Профиль для 1983"),
      "phone-guy",
    );
    await userEvent.click(screen.getByRole("button", { name: "Сохранить 1983" }));
    expect(await screen.findByText("Сохранено")).toBeTruthy();
    expect(screen.queryByLabelText("Admin password")).toBeNull();
    const put = fetchMock.mock.calls.find(
      ([path]) => path === "/admin/api/v1/voice-routes/1983",
    )!;
    expect(put[0]).toBe("/admin/api/v1/voice-routes/1983");
    expect(new Headers(put[1].headers).get("X-CSRF-Token")).toBe("");
  });

  it("shows an independent editable voice profile for a connected browser phone", async () => {
    const browserRoutes = { ...routes, browserExtensions: { "3454": "original" } };
    const saved = { ...browserRoutes, revision: 5, browserExtensions: { "3454": "phone-guy" } };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, { required: false }))
      .mockResolvedValueOnce(response(200, browserRoutes))
      .mockResolvedValueOnce(response(200, phonebook))
      .mockResolvedValueOnce(response(200, { ready: true, activeChannels: 0, endpoints: [] }))
      .mockResolvedValueOnce(response(200, { sessions: [{ nickname: "phoneguy123", extension: "3454", expiresAt: "2026-09-27T20:00:00Z" }] }))
      .mockResolvedValueOnce(response(200, saved))
      .mockResolvedValueOnce(response(200, { sessions: [{ nickname: "phoneguy123", extension: "3454", expiresAt: "2026-09-27T20:00:00Z" }] }))
      .mockResolvedValue(response(200, metrics));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: "Профили голоса" }));
    expect(await screen.findByText("phoneguy123")).toBeInTheDocument();
    expect(screen.getByText("3454")).toBeInTheDocument();
    expect(screen.getByText("Подключён")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/admin/api/v1/browser-phones", expect.any(Object));
    const select = await screen.findByRole("combobox", { name: "Профиль голоса · 3454" });
    expect(select).toHaveValue("original");
    await userEvent.selectOptions(select, "phone-guy");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Сохранено")).toBeInTheDocument();
    const put = fetchMock.mock.calls.find(([path]) => path === "/admin/api/v1/voice-routes/3454/browser");
    expect(put).toBeTruthy();
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ profile: "phone-guy", revision: 4 });
  });

  it("separates assigned physical phones, virtual placeholders, and browser phones", async () => {
    const configuredRoutes = {
      revision: 4,
      extensions: {
        "1983": "original",
        "1987": "original",
        "1988": "original",
        "2014": "original",
        "3454": "original",
      },
      browserExtensions: { "3454": "original" },
    };
    const inventory = {
      revision: 2,
      devices: [
        { mac: "00:11:22:33:44:55", label: "Grandstream desk", extension: "1983" },
        { mac: "00:11:22:33:44:66", label: "Yealink desk", extension: "1988" },
      ],
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response(200, { required: false }))
      .mockResolvedValueOnce(response(200, configuredRoutes))
      .mockResolvedValueOnce(response(200, inventory))
      .mockResolvedValueOnce(response(200, { ready: true, activeChannels: 0, endpoints: [] }))
      .mockResolvedValueOnce(response(200, { sessions: [{ nickname: "browser3454", extension: "3454", expiresAt: "2026-09-29T20:00:00Z" }] }))
      .mockResolvedValue(response(200, metrics));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "Профили голоса" }));
    const physical = screen.getByRole("region", { name: "Физические телефоны" });
    const virtual = screen.getByRole("region", { name: "Виртуальные номера" });
    const browsers = screen.getByRole("region", { name: "Браузерные телефоны" });

    expect(within(physical).getByText("1983")).toBeInTheDocument();
    expect(within(physical).getByText("1988")).toBeInTheDocument();
    expect(within(physical).getByText("Grandstream desk")).toBeInTheDocument();
    expect(within(physical).queryByText("1987")).not.toBeInTheDocument();
    expect(within(physical).queryByText("2014")).not.toBeInTheDocument();
    expect(within(virtual).getByText("1987")).toBeInTheDocument();
    expect(within(virtual).getByText("2014")).toBeInTheDocument();
    expect(within(virtual).queryByText("3454")).not.toBeInTheDocument();
    expect(within(browsers).getByText("browser3454")).toBeInTheDocument();
  });

  it("shows service unavailable when routes cannot be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response(503, { error: "config_unavailable" })),
    );
    render(<App />);
    expect(await screen.findByText(/Сервис недоступен/i)).toBeTruthy();
  });
});
