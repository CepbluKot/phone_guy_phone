import { afterEach, describe, expect, it, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";

const routes = {
  revision: 4,
  extensions: { "1983": "original", "1987": "phone-guy" },
};
const response = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("phone profile admin", () => {
  it("requires login, then loads and displays current phone profiles", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(response(401, { error: "unauthorized" }))
      .mockResolvedValueOnce(response(201, { csrfToken: "csrf-one" }))
      .mockResolvedValueOnce(response(200, routes));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeTruthy();
    await userEvent.type(screen.getByLabelText("Admin password"), "secret");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
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
      .mockResolvedValueOnce(response(401, { error: "unauthorized" }))
      .mockResolvedValueOnce(response(201, { csrfToken: "csrf-two" }))
      .mockResolvedValueOnce(response(200, routes))
      .mockResolvedValueOnce(response(200, updated));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.type(
      await screen.findByLabelText("Admin password"),
      "secret",
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    const select = await screen.findByLabelText("Profile for 1983");
    await userEvent.selectOptions(select, "phone-guy");
    await userEvent.click(screen.getByRole("button", { name: "Save 1983" }));
    expect(await screen.findByText("Saved")).toBeTruthy();
    const put = fetchMock.mock.calls[3];
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
      .mockResolvedValueOnce(response(401, { error: "unauthorized" }))
      .mockResolvedValueOnce(response(201, { csrfToken: "csrf-three" }))
      .mockResolvedValueOnce(response(200, routes))
      .mockResolvedValueOnce(response(409, { error: "stale_revision" }))
      .mockResolvedValueOnce(response(200, fresh));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await userEvent.type(
      await screen.findByLabelText("Admin password"),
      "secret",
    );
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await userEvent.selectOptions(
      await screen.findByLabelText("Profile for 1983"),
      "phone-guy",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save 1983" }));
    expect(await screen.findByText(/changed in another session/i)).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    expect(screen.getByLabelText("Profile for 1983")).toHaveValue("original");
  });

  it("shows service unavailable when routes cannot be loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response(503, { error: "config_unavailable" })),
    );
    render(<App />);
    expect(await screen.findByText(/service is unavailable/i)).toBeTruthy();
  });
});
