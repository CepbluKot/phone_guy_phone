import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DirectoryEntry, PhoneSession, phoneAPI } from "./api";
import { BrowserSIPSession, CallStatus, SIPStatus } from "./sipSession";
import "../design-system.css";
import "./phone.css";

const labels: Record<string, string> = {
  extension_busy: "Этот внутренний номер уже занят браузерным телефоном.",
  invalid_request: "Проверьте ник и внутренний номер.",
  session_not_found: "Сессия завершилась. Подключитесь снова.",
  phone_unavailable: "Телефонный сервис сейчас недоступен.",
  origin_forbidden: "Запрос отклонён: откройте страницу с адреса сервиса.",
  invalid_sip_uri: "Не удалось подготовить SIP-соединение.",
  invalid_call_target: "Выберите внутренний номер из списка.",
};

export function PhoneApp() {
  const [people, setPeople] = useState<DirectoryEntry[]>([]);
  const [audioInputs, setAudioInputs] = useState<MediaDeviceInfo[]>([]);
  const [audioOutputs, setAudioOutputs] = useState<MediaDeviceInfo[]>([]);
  const [inputDeviceId, setInputDeviceId] = useState("");
  const [outputDeviceId, setOutputDeviceId] = useState("");
  const [audioDeviceError, setAudioDeviceError] = useState("");
  const [nickname, setNickname] = useState("");
  const [extension, setExtension] = useState("");
  const [newExtensionMode, setNewExtensionMode] = useState(false);
  const [newExtension, setNewExtension] = useState("");
  const [target, setTarget] = useState("");
  const [session, setSession] = useState<PhoneSession>();
  const [registration, setRegistration] = useState<SIPStatus>("offline");
  const [callStatus, setCallStatus] = useState<CallStatus>("idle");
  const [peerExtension, setPeerExtension] = useState("");
  const [microphoneMuted, setMicrophoneMuted] = useState(false);
  const [speakerMuted, setSpeakerMuted] = useState(false);
  const [connectedAt, setConnectedAt] = useState<number>();
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [audioPlaybackError, setAudioPlaybackError] = useState("");
  const audioRef = useRef<HTMLAudioElement>(null);
  const sipRef = useRef<BrowserSIPSession | undefined>(undefined);
  const sessionIdRef = useRef("");
  const connected = registration === "registered";
  const targetOptions = useMemo(() => people.filter((person) => person.extension !== extension), [people, extension]);
  const activePeer = people.find((person) => person.extension === peerExtension);
  const activePeerName = activePeer?.nickname || peerExtension || "Внутренний номер";
  const mediaDevices = navigator.mediaDevices as (MediaDevices & { selectAudioOutput?: () => Promise<MediaDeviceInfo> }) | undefined;
  const supportsOutputSelection = typeof (audioRef.current as (HTMLAudioElement & { setSinkId?: (deviceId: string) => Promise<void> }) | null)?.setSinkId === "function";

  const refreshAudioDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter((device) => device.kind === "audioinput");
    const outputs = devices.filter((device) => device.kind === "audiooutput");
    setAudioInputs(inputs);
    setAudioOutputs(outputs);
    setInputDeviceId((current) => inputs.some((device) => device.deviceId === current) ? current : "");
    setOutputDeviceId((current) => outputs.some((device) => device.deviceId === current) ? current : "");
  }, []);

  useEffect(() => {
    void refreshAudioDevices().catch(() => undefined);
    const devices = navigator.mediaDevices;
    const onDeviceChange = () => void refreshAudioDevices().catch(() => undefined);
    devices?.addEventListener?.("devicechange", onDeviceChange);
    return () => devices?.removeEventListener?.("devicechange", onDeviceChange);
  }, [refreshAudioDevices]);

  useEffect(() => {
    if (!connectedAt) {
      setElapsedSeconds(0);
      return;
    }
    const updateElapsed = () => setElapsedSeconds(Math.floor((Date.now() - connectedAt) / 1000));
    updateElapsed();
    const timer = window.setInterval(updateElapsed, 1000);
    return () => window.clearInterval(timer);
  }, [connectedAt]);

  const refreshDirectory = useCallback(async () => {
    const result = await phoneAPI.directory();
    setPeople(result.people);
    setExtension((current) => current || result.people[0]?.extension || "");
    setTarget((current) => current || result.people.find((person) => person.extension !== extension)?.extension || "");
  }, [extension]);

  useEffect(() => {
    void refreshDirectory().catch(() => setError("Не удалось загрузить список внутренних номеров."));
  }, [refreshDirectory]);

  useEffect(() => {
    if (target && target !== extension && people.some((person) => person.extension === target)) return;
    setTarget(people.find((person) => person.extension !== extension)?.extension || "");
  }, [people, extension, target]);

  const endSession = useCallback(async (keepalive = false) => {
    const id = sessionIdRef.current;
    sessionIdRef.current = "";
    const sip = sipRef.current;
    sipRef.current = undefined;
    setSession(undefined);
    setRegistration("offline");
    setCallStatus("idle");
    if (sip) await Promise.resolve(sip.disconnect()).catch(() => undefined);
    if (id) await Promise.resolve(phoneAPI.release(id, keepalive)).catch(() => undefined);
  }, []);

  useEffect(() => {
    const releaseOnClose = () => {
      const id = sessionIdRef.current;
      if (id) void phoneAPI.release(id, true);
      void sipRef.current?.disconnect();
      sessionIdRef.current = "";
      sipRef.current = undefined;
    };
    window.addEventListener("pagehide", releaseOnClose);
    return () => window.removeEventListener("pagehide", releaseOnClose);
  }, []);

  useEffect(() => {
    if (!session?.sessionId) return;
    const timer = window.setInterval(() => {
      void phoneAPI.heartbeat(session.sessionId).catch(() => {
        setError("Соединение потеряно. Регистрация отключена.");
        void endSession();
      });
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [session?.sessionId, endSession]);

  const startSession = async (event: FormEvent) => {
    event.preventDefault();
    const selectedExtension = newExtensionMode ? newExtension : extension;
    setBusy(true);
    setError("");
    let claimed: Awaited<ReturnType<typeof phoneAPI.claim>> | undefined;
    try {
      const config = await phoneAPI.config();
      claimed = await phoneAPI.claim(nickname.trim(), selectedExtension, newExtensionMode);
      sessionIdRef.current = claimed.session.sessionId;
      const sip = new BrowserSIPSession(config.signalingUrl, setRegistration, (status, from) => {
        setCallStatus(status);
        if (from) setPeerExtension(from);
        if (status === "connected") setConnectedAt(Date.now());
        else setConnectedAt(undefined);
        if (status === "idle") {
          setPeerExtension("");
          setAudioPlaybackError("");
          setMicrophoneMuted(false);
          setSpeakerMuted(false);
          if (audioRef.current) audioRef.current.muted = false;
        }
      }, setAudioPlaybackError);
      sipRef.current = sip;
      await sip.connect(claimed.sip, audioRef.current!);
      setSession(claimed.session);
      setTarget((current) => current || people.find((person) => person.extension !== selectedExtension)?.extension || "");
    } catch (reason) {
      setError(labels[reason instanceof Error ? reason.message : ""] || "Не удалось подключить браузерный телефон.");
      await endSession();
    } finally {
      setBusy(false);
    }
  };

  const makeCall = async () => {
    setBusy(true);
    setError("");
    try {
      await sipRef.current?.call(target);
    } catch (reason) {
      setError(labels[reason instanceof Error ? reason.message : ""] || "Не удалось начать звонок.");
      setCallStatus("idle");
    } finally {
      setBusy(false);
    }
  };

  const stopCall = async () => {
    setBusy(true);
    try {
      let failed = false;
      try { await sipRef.current?.hangup(); } catch { failed = true; }
      const sessionId = sessionIdRef.current;
      if (sessionId) {
        try { await phoneAPI.hangup(sessionId); } catch { failed = true; }
      }
      if (failed) setError("Не удалось завершить вызов. Попробуйте отключить браузерный телефон.");
    } finally { setBusy(false); }
  };

  const toggleMicrophone = () => {
    const muted = !microphoneMuted;
    sipRef.current?.setMicrophoneMuted(muted);
    setMicrophoneMuted(muted);
  };

  const toggleSpeaker = () => {
    const muted = !speakerMuted;
    if (audioRef.current) audioRef.current.muted = muted;
    setSpeakerMuted(muted);
  };

  const labelFor = (person: DirectoryEntry) => {
    const endpoints: string[] = [];
    if (person.physicalPhone) endpoints.push(`физический телефон · ${person.physicalPhone}`);
    if (person.nickname) endpoints.push(`браузер · ${person.nickname} · ${person.active ? "в сети" : "не в сети"}`);
    if (!endpoints.length) endpoints.push("нет подключённого устройства");
    return `${person.extension} · ${endpoints.join(" + ")}`;
  };

  const formatElapsed = (seconds: number) => {
    const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
    const remainder = (seconds % 60).toString().padStart(2, "0");
    return `${minutes}:${remainder}`;
  };

  const showAudioDevices = async () => {
    const devices = navigator.mediaDevices;
    if (!devices?.getUserMedia) {
      setAudioDeviceError("Браузер не предоставляет доступ к аудиоустройствам.");
      return;
    }
    try {
      const stream = await devices.getUserMedia({ audio: true, video: false });
      stream.getTracks().forEach((track) => track.stop());
      await refreshAudioDevices();
      setAudioDeviceError("");
    } catch {
      setAudioDeviceError("Не удалось получить список микрофонов. Проверьте разрешение на микрофон в браузере.");
    }
  };

  const chooseInputDevice = async (deviceId: string) => {
    try {
      await sipRef.current?.setInputDevice(deviceId);
      setInputDeviceId(deviceId);
      setAudioDeviceError("");
      await refreshAudioDevices();
    } catch {
      setAudioDeviceError("Не удалось переключить микрофон. Проверьте доступ к выбранному устройству.");
    }
  };

  const chooseOutputDevice = async (deviceId: string) => {
    const audio = audioRef.current as (HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> }) | null;
    if (!audio?.setSinkId) {
      setAudioDeviceError("Выбор колонок не поддерживается этим браузером.");
      return;
    }
    try {
      await audio.setSinkId(deviceId);
      setOutputDeviceId(deviceId);
      setAudioDeviceError("");
    } catch {
      setAudioDeviceError("Не удалось переключить колонки. Выберите устройство, доступное этому сайту.");
    }
  };

  const chooseAnotherOutput = async () => {
    if (!mediaDevices?.selectAudioOutput) {
      setAudioDeviceError("Браузер не поддерживает выбор дополнительных колонок.");
      return;
    }
    try {
      const device = await mediaDevices.selectAudioOutput();
      await refreshAudioDevices();
      await chooseOutputDevice(device.deviceId);
    } catch {
      setAudioDeviceError("Не удалось открыть выбор колонок. Проверьте разрешение браузера на выбор устройства вывода.");
    }
  };

  const audioSettings = (
    <div className="phone-audio-settings">
      <div className="phone-audio-settings-heading"><h3>Аудиоустройства</h3><button className="phone-device-refresh" type="button" onClick={() => void showAudioDevices()}>Показать устройства</button></div>
      <div className="phone-audio-device-grid">
        <label>Микрофон<select aria-label="Микрофон" value={inputDeviceId} onChange={(event) => void chooseInputDevice(event.target.value)}><option value="">Системный по умолчанию</option>{audioInputs.filter((device) => device.deviceId).map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Микрофон ${index + 1}`}</option>)}</select></label>
        <label>Колонки<select aria-label="Колонки" value={outputDeviceId} disabled={!supportsOutputSelection} onChange={(event) => void chooseOutputDevice(event.target.value)}><option value="">Системные по умолчанию</option>{audioOutputs.filter((device) => device.deviceId).map((device, index) => <option key={device.deviceId} value={device.deviceId}>{device.label || `Колонки ${index + 1}`}</option>)}</select></label>
      </div>
      {mediaDevices?.selectAudioOutput && supportsOutputSelection && <button className="phone-device-refresh phone-output-picker" type="button" onClick={() => void chooseAnotherOutput()}>Другие колонки…</button>}
      {!supportsOutputSelection && <p className="phone-device-hint">Этот браузер использует системные колонки по умолчанию.</p>}
      {audioDeviceError && <p className="phone-device-error" role="alert">{audioDeviceError}</p>}
    </div>
  );

  return (
    <main className="phone-shell">
      <div
        className={`phone-app-background${callStatus === "connected" ? " is-blurred" : ""}`}
        aria-hidden={callStatus === "connected" ? true : undefined}
        inert={callStatus === "connected"}
      >
        <header className="phone-topbar">
          <a className="phone-brand" href="/phone/" aria-label="Voice phone home"><span className="phone-mark">V</span><span>Voice desk</span></a>
          <a className="phone-admin-link" href="/admin/">Администрирование <span aria-hidden="true">↗</span></a>
        </header>
        <div className="phone-content">
          <section className="phone-heading">
            <p className="phone-eyebrow">ВНУТРЕННЯЯ СВЯЗЬ</p>
            <h1>Телефон</h1>
            <p>Звоните коллегам из браузера или принимайте звонки на внутренний номер.</p>
          </section>

          {!session ? (
            <section className="phone-card phone-setup-card">
            <div className="phone-card-heading"><div><span className="phone-step">01</span><h2>Подключить этот браузер</h2></div><span className="phone-status-pill is-offline"><i /> Не подключён</span></div>
            <form onSubmit={startSession} className="phone-form">
              <label>Ваш ник<input value={nickname} onChange={(event) => setNickname(event.target.value)} maxLength={48} autoComplete="nickname" required placeholder="phoneguy123" /></label>
              <label>Внутренний номер<select value={newExtensionMode ? "new" : "existing"} onChange={(event) => setNewExtensionMode(event.target.value === "new")}><option value="existing">Выбрать существующий</option><option value="new">Придумать новый</option></select></label>
              {newExtensionMode ? <label>Новый номер<input aria-label="Новый внутренний номер" aria-describedby="new-extension-hint" type="text" inputMode="numeric" autoComplete="off" pattern="(?:[3-9][0-9]{2}|[3-9][0-9]{3})" minLength={3} maxLength={4} value={newExtension} onChange={(event) => setNewExtension(event.target.value.replace(/\D/g, "").slice(0, 4))} placeholder="Например, 345" required /><span id="new-extension-hint" className="phone-number-hint">Можно придумать номер из трёх или четырёх цифр. Служебный номер 600 зарезервирован.</span></label> : <label>Выберите номер<select aria-label="Выберите номер" value={extension} onChange={(event) => setExtension(event.target.value)} required>{people.map((person) => <option key={person.extension} value={person.extension}>{person.extension}{person.nickname ? ` · ${person.nickname}` : " · свободен"}{person.active ? " · браузер занят" : ""}</option>)}</select></label>}
              <button className="phone-primary-button" type="submit" disabled={busy || (newExtensionMode ? !/^(?:[3-9]\d{2}|[3-9]\d{3})$/.test(newExtension) || newExtension === "600" : !extension)}>{busy ? "Подключаем…" : "Подключиться"}<span aria-hidden="true">→</span></button>
            </form>
            <p className="phone-helper">На одном внутреннем номере может быть один активный браузер. Физический аппарат продолжит работать.</p>
            </section>
          ) : (
            <>
              <section className="phone-card phone-connected-card">
              <div className="phone-card-heading"><div><span className="phone-step">01</span><h2>{session.nickname} <small>· {session.extension}</small></h2></div><span className={`phone-status-pill ${connected ? "is-online" : "is-offline"}`}><i />{connected ? "Готов принимать звонки" : registration === "connecting" ? "Подключаем SIP…" : "SIP отключён"}</span></div>
              <button className="phone-text-button" onClick={() => void endSession()} disabled={busy}>Отключить этот браузер</button>
              </section>
              <section className="phone-card phone-call-card">
              <div className="phone-card-heading"><div><span className="phone-step">02</span><h2>Звонок</h2></div><span className="phone-call-state">{callStatus === "idle" ? "Нет активного вызова" : callStatus === "calling" ? `Вызываем ${target}` : callStatus === "ringing" ? `Входящий · ${peerExtension || "внутренний номер"}` : "Разговор"}</span></div>
                {callStatus === "ringing" ? <div className="phone-call-actions"><button className="phone-primary-button" onClick={() => void sipRef.current?.answer()}>Ответить <span>↗</span></button><button className="phone-secondary-button" onClick={() => void sipRef.current?.decline()}>Отклонить</button></div> : callStatus !== "idle" ? <button className="phone-hangup-button" onClick={() => void stopCall()} disabled={busy}>Завершить звонок <span>×</span></button> : <div className="phone-dial-row"><label className="phone-target-select">Кому позвонить<select value={target} onChange={(event) => setTarget(event.target.value)}>{targetOptions.map((person) => <option key={person.extension} value={person.extension}>{labelFor(person)}</option>)}</select></label><button className="phone-primary-button" onClick={() => void makeCall()} disabled={!connected || busy || !target}>Позвонить <span>↗</span></button></div>}
                {callStatus !== "connected" && audioSettings}
              </section>
            </>
          )}

          {error && <div role="alert" className="phone-error"><span>!</span>{error}</div>}
          <footer className="phone-footer"><span><i className={connected ? "is-online" : ""} />{connected ? "Сигнализация защищена TLS" : "Подключение доступно в частной сети"}</span><a href="/admin/">Управление профилями →</a></footer>
        </div>
      </div>
      {callStatus === "connected" && (
        <div className="phone-call-overlay">
          <section className="phone-call-modal" role="dialog" aria-modal="true" aria-labelledby="active-call-name">
            <div className="phone-call-avatar" aria-hidden="true">{activePeerName.slice(0, 1).toLocaleUpperCase()}</div>
            <span className="phone-call-connected"><i /> Идёт разговор</span>
            <h2 id="active-call-name">{activePeerName}</h2>
            <p className="phone-call-extension">Внутренний номер · {peerExtension || target}</p>
            <p className="phone-call-duration" aria-label={`Длительность звонка ${formatElapsed(elapsedSeconds)}`}>{formatElapsed(elapsedSeconds)}</p>
            <div className="phone-call-audio-controls" aria-label="Управление звуком звонка">
              <button type="button" className={`phone-call-audio-toggle${microphoneMuted ? " is-muted" : ""}`} aria-pressed={microphoneMuted} onClick={toggleMicrophone}>
                {microphoneMuted ? "Включить микрофон" : "Выключить микрофон"}
              </button>
              <button type="button" className={`phone-call-audio-toggle${speakerMuted ? " is-muted" : ""}`} aria-pressed={speakerMuted} onClick={toggleSpeaker}>
                {speakerMuted ? "Включить звук собеседника" : "Выключить звук собеседника"}
              </button>
            </div>
            {audioSettings}
            {audioPlaybackError && <><p className="phone-call-audio-error" role="alert">{audioPlaybackError}</p><button className="phone-audio-retry" onClick={() => void sipRef.current?.resumeAudio()}>Включить звук</button></>}
            <button className="phone-hangup-button phone-modal-hangup" onClick={() => void stopCall()} disabled={busy}>Завершить звонок <span>×</span></button>
          </section>
        </div>
      )}
      <audio ref={audioRef} autoPlay playsInline muted={speakerMuted} />
    </main>
  );
}
