import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PhoneApp } from "./PhoneApp";
import { phoneAPI } from "./api";
import { BrowserSIPSession } from "./sipSession";

vi.mock("./api", () => ({ phoneAPI: { config: vi.fn(), directory: vi.fn(), claim: vi.fn(), heartbeat: vi.fn(), release: vi.fn() } }));
const sipMocks = vi.hoisted(() => ({ connect: vi.fn(), call: vi.fn(), disconnect: vi.fn(), hangup: vi.fn(), answer: vi.fn(), decline: vi.fn(), resumeAudio: vi.fn(), setInputDevice: vi.fn(), iceServers: undefined as RTCIceServer[] | undefined, onCall: undefined as ((status: string, peer?: string) => void) | undefined, onAudioError: undefined as ((message: string) => void) | undefined }));
vi.mock("./sipSession", () => ({ BrowserSIPSession: class { constructor(_url: string, private registered: (state: string) => void, onCall: (status: string, peer?: string) => void, onAudioError: (message: string) => void, iceServers?: RTCIceServer[]) { sipMocks.onCall = onCall; sipMocks.onAudioError = onAudioError; sipMocks.iceServers = iceServers; } async connect(){this.registered("registered");await sipMocks.connect()} async call(target: string){sipMocks.onCall?.("calling",target);await sipMocks.call(target)} async disconnect(){await sipMocks.disconnect()} async hangup(){await sipMocks.hangup()} async answer(){await sipMocks.answer()} async decline(){await sipMocks.decline()} async resumeAudio(){await sipMocks.resumeAudio()} async setInputDevice(deviceId: string){await sipMocks.setInputDevice(deviceId)} } }));

const sessionResponse={session:{sessionId:"opaque-token",nickname:"Alice",extension:"1983",expiresAt:"2026-09-26T12:00:30Z"},sip:{uri:"sip:web-abc@vm-voice-1.lan.awesomeio.ru",username:"web-abc",password:"never-store-this",endpoint:"web-abc"}};

describe("browser phone",()=>{
  let originalMediaDevices: PropertyDescriptor | undefined;
  let originalSetSinkId: PropertyDescriptor | undefined;
  beforeEach(()=>{
    originalMediaDevices=Object.getOwnPropertyDescriptor(navigator,"mediaDevices");
    originalSetSinkId=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,"setSinkId");
    Object.defineProperty(navigator,"mediaDevices",{configurable:true,value:{enumerateDevices:vi.fn().mockResolvedValue([
      {deviceId:"mic-usb",kind:"audioinput",label:"USB microphone"},
      {deviceId:"speaker-usb",kind:"audiooutput",label:"USB headset"},
    ]),addEventListener:vi.fn(),removeEventListener:vi.fn()}});
    Object.defineProperty(HTMLMediaElement.prototype,"setSinkId",{configurable:true,value:vi.fn().mockResolvedValue(undefined)});
    vi.mocked(phoneAPI.directory).mockResolvedValue({people:[{nickname:"Alice",extension:"1983",active:false},{nickname:"Bob",extension:"1988",active:false,physicalPhone:"Grandstream"}]});
    vi.mocked(phoneAPI.config).mockResolvedValue({signalingUrl:"wss://vm-voice-1.lan.awesomeio.ru/ws/phone-signaling"});
    vi.mocked(phoneAPI.claim).mockResolvedValue(sessionResponse);
    vi.mocked(phoneAPI.release).mockResolvedValue(undefined);
    vi.mocked(phoneAPI.heartbeat).mockResolvedValue(sessionResponse.session);
    sipMocks.connect.mockResolvedValue(undefined);sipMocks.disconnect.mockResolvedValue(undefined);sipMocks.call.mockResolvedValue(undefined);sipMocks.hangup.mockResolvedValue(undefined);
  });
  afterEach(()=>{cleanup();vi.useRealTimers();vi.clearAllMocks();if(originalMediaDevices)Object.defineProperty(navigator,"mediaDevices",originalMediaDevices);else delete (navigator as unknown as {mediaDevices?:MediaDevices}).mediaDevices;if(originalSetSinkId)Object.defineProperty(HTMLMediaElement.prototype,"setSinkId",originalSetSinkId);else delete (HTMLMediaElement.prototype as unknown as {setSinkId?:unknown}).setSinkId});
  it("requires a nickname and a configured extension",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));expect(phoneAPI.claim).not.toHaveBeenCalled();
  });
  it("passes authenticated TURN ICE servers into the public WebRTC session",async()=>{
    const iceServers=[{urls:["turns:phone.awesomeio.ru:5349?transport=tcp"],username:"1800001800:voice-phone",credential:"ephemeral-credential"}];
    vi.mocked(phoneAPI.config).mockResolvedValue({signalingUrl:"wss://phone.awesomeio.ru/ws/phone-signaling",sipDomain:"phone.awesomeio.ru",iceServers});
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");
    expect(sipMocks.iceServers).toEqual(iceServers);
  });
  it("shows the public access hint on the public phone domain",async()=>{
    vi.stubGlobal("location",new URL("https://phone.awesomeio.ru/phone/"));
    render(<PhoneApp/>);
    expect(await screen.findByText("Публичный доступ защищён")).not.toBeNull();
  });
  it("registers the browser and calls only another configured internal number",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");expect(BrowserSIPSession).toBeDefined();
    expect(screen.getByRole("option",{name:/Bob/})).not.toBeNull();expect(screen.queryByRole("option",{name:/Alice/})).toBeNull();fireEvent.click(screen.getByRole("button",{name:/Позвонить/}));await waitFor(()=>expect(sipMocks.call).toHaveBeenCalledWith("1988"));
  });
  it("keeps virtual placeholders out of call targets but available for browser registration",async()=>{
    vi.mocked(phoneAPI.directory).mockResolvedValue({people:[
      {nickname:"Alice",extension:"1983",active:false},
      {nickname:"",extension:"1987",active:false},
      {nickname:"Bob",extension:"1988",active:false,physicalPhone:"Grandstream",physicalStatus:"offline"},
      {nickname:"Carol",extension:"3454",active:true},
    ]});
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");
    expect(screen.getByRole("option",{name:/1987/})).not.toBeNull();
    fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");
    expect(screen.queryByRole("option",{name:/1987/})).toBeNull();
    expect(screen.getByRole("option",{name:/1988.*физический телефон/})).not.toBeNull();
    expect(screen.getByRole("option",{name:/3454.*браузер/})).not.toBeNull();
  });
  it("hides extensions assigned to physical phones from browser registration",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");
    expect(screen.queryByRole("option",{name:/1988/})).toBeNull();
    expect(screen.getByRole("option",{name:/1983/})).not.toBeNull();
  });
  it("explains when the API rejects a physical phone extension",async()=>{
    vi.mocked(phoneAPI.claim).mockRejectedValue(new Error("physical_phone_extension_reserved"));
    vi.mocked(phoneAPI.directory).mockResolvedValue({people:[{nickname:"Physical",extension:"1983",active:false,physicalPhone:"Yealink"},{nickname:"Alice",extension:"1987",active:false}]});
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");
    fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});
    fireEvent.change(screen.getByLabelText("Выберите номер"),{target:{value:"1987"}});
    fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));
    expect((await screen.findByRole("alert")).textContent).toContain("Этот номер назначен физическому телефону");
    expect(phoneAPI.claim).toHaveBeenCalledWith("Alice","1987",false);
  });
  it("refreshes browser presence while the phone page stays open",async()=>{
    vi.useFakeTimers();
    vi.mocked(phoneAPI.directory)
      .mockResolvedValueOnce({people:[{nickname:"Alice",extension:"1983",active:false},{nickname:"Bob",extension:"1988",active:false,physicalPhone:"Grandstream",physicalStatus:"offline"}]})
      .mockResolvedValueOnce({people:[{nickname:"Alice",extension:"1983",active:false},{nickname:"Bob",extension:"1988",active:false,physicalPhone:"Grandstream",physicalStatus:"offline"}]})
      .mockResolvedValue({people:[{nickname:"Alice",extension:"1983",active:false},{nickname:"Bob",extension:"1988",active:true,physicalPhone:"Grandstream",physicalStatus:"online"}]});
    render(<PhoneApp/>);
    await act(async()=>{await Promise.resolve()});
    fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});
    fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});
    fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));
    await act(async()=>{await Promise.resolve();await Promise.resolve()});
    expect(screen.getByRole("option",{name:/1988 · физический телефон · Grandstream · не в сети/})).not.toBeNull();
    await act(async()=>{await vi.advanceTimersByTimeAsync(2000)});
    expect(phoneAPI.directory).toHaveBeenCalledTimes(3);
    expect(screen.getByRole("option",{name:/1988 · физический телефон · Grandstream · в сети.*браузер · Bob · в сети/})).not.toBeNull();
  });
  it("shows an outgoing-call modal and switches it to the active-call modal",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");
    fireEvent.click(screen.getByRole("button",{name:/Позвонить/}));await waitFor(()=>expect(sipMocks.call).toHaveBeenCalledWith("1988"));act(()=>sipMocks.onCall?.("calling","1988"));expect(screen.getByRole("dialog").textContent).toContain("Звоним");expect(screen.getByRole("dialog").textContent).toContain("Отменить вызов");
    act(()=>sipMocks.onCall?.("connected"));
    const dialog=await screen.findByRole("dialog");expect(dialog.getAttribute("aria-modal")).toBe("true");expect(dialog.textContent).toContain("Bob");expect(dialog.textContent).toContain("00:00");expect(screen.getByRole("button",{name:/Завершить звонок/})).not.toBeNull();expect(document.querySelector(".phone-app-background")?.getAttribute("aria-hidden")).toBe("true");expect(screen.getByLabelText("Микрофон")).not.toBeNull();
    fireEvent.change(screen.getByLabelText("Микрофон"),{target:{value:"mic-usb"}});await waitFor(()=>expect(sipMocks.setInputDevice).toHaveBeenCalledWith("mic-usb"));
  });
  it("shows blocked speaker playback and lets the user retry it",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");
    act(()=>sipMocks.onCall?.("connected"));const dialog=await screen.findByRole("dialog");act(()=>sipMocks.onAudioError?.("Браузер заблокировал воспроизведение звука."));expect(dialog.textContent).toContain("Браузер заблокировал воспроизведение звука.");fireEvent.click(screen.getByRole("button",{name:"Включить звук"}));await waitFor(()=>expect(sipMocks.resumeAudio).toHaveBeenCalled());
  });
  it("lists microphones and speakers and applies the selected devices",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.change(screen.getByLabelText("Внутренний номер"),{target:{value:"1983"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");
    expect(await screen.findByRole("option",{name:"USB microphone"})).not.toBeNull();expect(screen.getByRole("option",{name:"USB headset"})).not.toBeNull();
    fireEvent.change(screen.getByLabelText("Микрофон"),{target:{value:"mic-usb"}});await waitFor(()=>expect(sipMocks.setInputDevice).toHaveBeenCalledWith("mic-usb"));
    fireEvent.change(screen.getByLabelText("Колонки"),{target:{value:"speaker-usb"}});await waitFor(()=>expect(HTMLMediaElement.prototype.setSinkId).toHaveBeenCalledWith("speaker-usb"));
  });
  it("releases the session and keeps session credentials out of browser storage",async()=>{
    render(<PhoneApp/>);await screen.findByLabelText("Ваш ник");fireEvent.change(screen.getByLabelText("Ваш ник"),{target:{value:"Alice"}});fireEvent.click(screen.getByRole("button",{name:/Подключиться/}));await screen.findByText("Готов принимать звонки");fireEvent.click(screen.getByRole("button",{name:/Отключить этот браузер/}));await waitFor(()=>expect(phoneAPI.release).toHaveBeenCalledWith("opaque-token",false));expect(localStorage.length).toBe(0);
  });
});
