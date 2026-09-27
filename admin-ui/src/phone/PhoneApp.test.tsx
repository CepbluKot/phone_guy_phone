import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PhoneApp } from "./PhoneApp";
import { phoneAPI } from "./api";
import { BrowserSIPSession } from "./sipSession";

vi.mock("./api", () => ({ phoneAPI: { config: vi.fn(), directory: vi.fn(), claim: vi.fn(), heartbeat: vi.fn(), release: vi.fn() } }));
const sipMocks = vi.hoisted(() => ({ connect: vi.fn(), call: vi.fn(), disconnect: vi.fn(), hangup: vi.fn(), answer: vi.fn(), decline: vi.fn() }));
vi.mock("./sipSession", () => ({ BrowserSIPSession: class { constructor(_url: string, private registered: (state: string) => void) {} async connect(){this.registered("registered");await sipMocks.connect()} async call(target: string){await sipMocks.call(target)} async disconnect(){await sipMocks.disconnect()} async hangup(){await sipMocks.hangup()} async answer(){await sipMocks.answer()} async decline(){await sipMocks.decline()} } }));

const sessionResponse={session:{sessionId:"opaque-token",nickname:"Alice",extension:"1983",expiresAt:"2026-09-26T12:00:30Z"},sip:{uri:"sip:web-abc@vm-voice-1.lan.awesomeio.ru",username:"web-abc",password:"never-store-this",endpoint:"web-abc"}};

describe("browser phone",()=>{
  beforeEach(()=>{
    vi.mocked(phoneAPI.directory).mockResolvedValue({people:[{nickname:"Alice",extension:"1983",active:false},{nickname:"Bob",extension:"1988",active:false}]});
    vi.mocked(phoneAPI.config).mockResolvedValue({signalingUrl:"wss://vm-voice-1.lan.awesomeio.ru/ws/phone-signaling"});
    vi.mocked(phoneAPI.claim).mockResolvedValue(sessionResponse);
    vi.mocked(phoneAPI.release).mockResolvedValue(undefined);
    vi.mocked(phoneAPI.heartbeat).mockResolvedValue(sessionResponse.session);
    sipMocks.connect.mockResolvedValue(undefined);sipMocks.disconnect.mockResolvedValue(undefined);sipMocks.call.mockResolvedValue(undefined);sipMocks.hangup.mockResolvedValue(undefined);
  });
  afterEach(()=>{cleanup();vi.clearAllMocks()});
  it("requires a nickname and a configured extension",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));expect(phoneAPI.claim).not.toHaveBeenCalled();
  });
  it("registers the browser and calls only another configured internal number",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");expect(BrowserSIPSession).toBeDefined();
    expect(screen.getByRole("option",{name:/Bob/})).not.toBeNull();expect(screen.queryByRole("option",{name:/Alice/})).toBeNull();fireEvent.click(screen.getByRole("button",{name:/Позвонить/}));await waitFor(()=>expect(sipMocks.call).toHaveBeenCalledWith("1988"));
  });
  it("releases the session and keeps session credentials out of browser storage",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");fireEvent.click(screen.getByRole("button",{name:/Отключить этот браузер/}));await waitFor(()=>expect(phoneAPI.release).toHaveBeenCalledWith("opaque-token",false));expect(localStorage.length).toBe(0);
  });
});
