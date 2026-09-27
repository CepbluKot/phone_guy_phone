import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, BrowserPhone, MetricsSnapshot, PhoneDevice, PhonebookSnapshot, Profile, RouteSnapshot } from "./api";
import "./styles.css";

type Language = "en" | "ru";
const copy = {
  en: {
    signIn: "Sign in",
    password: "Admin password",
    loginHint: "Private phone settings",
    loading: "Loading phone profiles…",
    unavailable: "Service is unavailable. Try again in a moment.",
    badPassword: "Password is incorrect.",
    title: "Phone profiles",
    service: "Private service",
    subtitle: "Choose how each phone sends its voice into calls.",
    phone: "Phone",
    role: "Voice profile",
    original: "Original voice",
    phoneGuy: "Phone Guy",
    save: "Save",
    saved: "Saved",
    stale:
      "Profile was changed in another session. The current settings have been reloaded.",
    saveError: "Could not save this profile. Try again.",
    phones: "Phones",
    voiceProfiles: "Voice profiles",
    physicalPhones: "Physical phones",
    existingPhones: "Existing phones",
    newUnassigned: "New / unassigned",
    physicalTitle: "Phone to extension map",
    assignedHeading: "Assigned phones",
    unassignedHeading: "New / unassigned phones",
    systemLoad: "System load",
    device: "Device",
    status: "Status",
    historyAvailable: "Seen in history",
    historyMissing: "No observation",
    loadSummary: "Current voice processing service state.",
    mappingOnly: "This table is an admin inventory. Saving a number here does not change the SIP account on the phone.",
    mac: "MAC address",
    lastAddress: "Last observed IP",
    lastObserved: "Last observed",
    unknown: "Unknown",
    unassigned: "Unassigned",
    addDevice: "Add phone",
    label: "Device label",
    assignmentSaved: "Mapping saved",
    mappingError: "Could not save this mapping.",
    revision: "Registry revision",
    stalePhonebook: "The mapping changed elsewhere. The latest list was loaded.",
    noPhones: "No physical phones in the inventory yet.",
    noUnassigned: "No unassigned phones in the inventory.",
    manualInventory: "These records are entered manually; automatic network discovery is not configured.",
    signOut: "Sign out",
    collapseSidebar: "Collapse sidebar",
    expandSidebar: "Expand sidebar",
    load: "Load",
    allowedNow: "Allowed now",
    estimate: "Measured estimate",
    notMeasured: "Not measured",
    worker: "RVC worker",
    callsActive: "Processed calls active",
    queue: "Queued windows",
    latency: "Block processing time (recent)",
    p50: "p50",
    p95: "p95",
    updated: "Updated",
    unavailableMetric: "Unavailable",
    staleMetric: "Stale",
    browserPhones: "Browser phones",
    noBrowserPhones: "No active browser phones.",
    browserConnected: "Connected",
    openPhone: "Open browser phone →",
  },
  ru: {
    signIn: "Войти",
    password: "Пароль администратора",
    loginHint: "Приватные настройки телефонов",
    loading: "Загружаем профили телефонов…",
    unavailable: "Сервис недоступен. Попробуйте чуть позже.",
    badPassword: "Неверный пароль.",
    title: "Профили телефонов",
    service: "Приватный сервис",
    subtitle: "Выберите обработку голоса для каждого телефона.",
    phone: "Телефон",
    role: "Профиль голоса",
    original: "Оригинальный голос",
    phoneGuy: "Phone Guy",
    save: "Сохранить",
    saved: "Сохранено",
    stale: "Профиль изменился в другой сессии. Текущие настройки обновлены.",
    saveError: "Не удалось сохранить профиль. Попробуйте ещё раз.",
    phones: "Телефоны",
    voiceProfiles: "Профили голоса",
    physicalPhones: "Физические телефоны",
    existingPhones: "Назначенные телефоны",
    newUnassigned: "Новые / без номера",
    physicalTitle: "Привязка телефона к номеру",
    assignedHeading: "Назначенные телефоны",
    unassignedHeading: "Новые / неназначенные телефоны",
    systemLoad: "Нагрузка системы",
    device: "Устройство",
    status: "Статус",
    historyAvailable: "Есть в истории",
    historyMissing: "Нет наблюдений",
    loadSummary: "Текущее состояние сервиса обработки голоса.",
    mappingOnly: "Это реестр в админке. Сохранение номера здесь не меняет SIP-аккаунт на самом телефоне.",
    mac: "MAC-адрес",
    lastAddress: "Последний замеченный IP",
    lastObserved: "Последнее наблюдение",
    unknown: "Неизвестно",
    unassigned: "Не назначен",
    addDevice: "Добавить телефон",
    label: "Название устройства",
    assignmentSaved: "Привязка сохранена",
    mappingError: "Не удалось сохранить привязку.",
    revision: "Версия реестра",
    stalePhonebook: "Реестр изменился в другой сессии. Список обновлён.",
    noPhones: "Физических телефонов пока нет в реестре.",
    noUnassigned: "В реестре нет телефонов без номера.",
    manualInventory: "Эти записи добавляются вручную; автоматическое обнаружение в сети не настроено.",
    signOut: "Выйти",
    collapseSidebar: "Свернуть меню",
    expandSidebar: "Развернуть меню",
    load: "Нагрузка",
    allowedNow: "Разрешено сейчас",
    estimate: "Измеренная оценка",
    notMeasured: "Не измерено",
    worker: "RVC-обработчик",
    callsActive: "Активные обработанные звонки",
    queue: "Окна в очереди",
    latency: "Время обработки блока (недавнее)",
    p50: "p50",
    p95: "p95",
    updated: "Обновлено",
    unavailableMetric: "Нет данных",
    staleMetric: "Данные устарели",
    browserPhones: "Браузерные телефоны",
    noBrowserPhones: "Нет подключённых браузеров.",
    browserConnected: "Подключён",
    openPhone: "Открыть браузерный телефон →",
  },
} satisfies Record<Language, Record<string, string>>;

export default function App() {
  const [language, setLanguage] = useState<Language>("ru");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      const saved = window.localStorage.getItem("voice-control-sidebar-collapsed");
      if (saved !== null) return saved === "true";
    } catch {
      // Use the viewport default if browser storage is unavailable.
    }
    return window.innerWidth <= 850;
  });
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [snapshot, setSnapshot] = useState<RouteSnapshot | null>(null);
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null);
  const [browserPhones, setBrowserPhones] = useState<BrowserPhone[]>([]);
  const [page, setPage] = useState<"phones" | "existing" | "unassigned" | "profiles" | "load">("phones");
  const [phonebook, setPhonebook] = useState<PhonebookSnapshot | null>(null);
  const [phoneDrafts, setPhoneDrafts] = useState<Record<string, { label: string; extension: string }>>({});
  const [phoneMessages, setPhoneMessages] = useState<Record<string, string>>({});
  const [phoneSaving, setPhoneSaving] = useState<string | null>(null);
  const [newPhone, setNewPhone] = useState({ mac: "", label: "", extension: "" });
  const [addingPhone, setAddingPhone] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Profile>>({});
  const [status, setStatus] = useState<
    "loading" | "login" | "ready" | "unavailable"
  >("loading");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState(false);
  const [messages, setMessages] = useState<
    Partial<Record<string, "saved" | "stale" | "error">>
  >({});
  const [saving, setSaving] = useState<string | null>(null);
  const t = copy[language];

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  useEffect(() => {
    try {
      window.localStorage.setItem("voice-control-sidebar-collapsed", String(sidebarCollapsed));
    } catch {
      // Sidebar controls remain available when browser storage is unavailable.
    }
  }, [sidebarCollapsed]);

  const loadRoutes = useCallback(async () => {
    try {
      const routes = await api.routes();
      const inventory = await api.phones();
      setSnapshot(routes);
      setDrafts(routes.extensions);
      setPhonebook(inventory);
      setPhoneDrafts(Object.fromEntries(inventory.devices.map((device) => [device.mac, { label: device.label, extension: device.extension ?? "" }])));
      setMessages({});
      setStatus("ready");
    } catch (error) {
      setStatus(
        error instanceof ApiError && error.status === 401
          ? "login"
          : "unavailable",
      );
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const mode = await api.authMode();
        setAuthRequired(mode.required);
        await loadRoutes();
      } catch {
        setStatus("unavailable");
      }
    })();
  }, [loadRoutes]);

  useEffect(() => {
    if (status !== "ready") return;
    let disposed = false;
    const refresh = async () => {
      try { const value = await api.metrics(); if (!disposed) setMetrics(value); }
      catch { if (!disposed) setMetrics(null); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [status]);

  useEffect(() => {
    if (status !== "ready") return;
    let disposed = false;
    const refresh = async () => { try { const value = await api.browserPhones(); if (!disposed) setBrowserPhones(Array.isArray(value.sessions) ? value.sessions : []); } catch { if (!disposed) setBrowserPhones([]); } };
    void refresh(); const timer=window.setInterval(()=>void refresh(),5000);return()=>{disposed=true;window.clearInterval(timer)};
  }, [status]);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoginError(false);
    try {
      const session = await api.login(password);
      setCsrfToken(session.csrfToken);
      setPassword("");
      await loadRoutes();
    } catch (error) {
      if (error instanceof ApiError && error.status === 401)
        setLoginError(true);
      else setStatus("unavailable");
    }
  }

  async function signOut() {
    if (!csrfToken) return;
    try {
      await api.logout(csrfToken);
    } finally {
      setCsrfToken(null);
      setSnapshot(null);
      setPhonebook(null);
      setStatus("login");
    }
  }

  async function save(extension: string) {
    if (!snapshot || (authRequired && !csrfToken)) return;
    const profile = drafts[extension];
    setSaving(extension);
    setMessages((current) => ({ ...current, [extension]: undefined }));
    try {
      const next = await api.update(
        extension,
        profile,
        snapshot.revision,
        csrfToken ?? "",
      );
      setSnapshot(next);
      setDrafts(next.extensions);
      setMessages((current) => ({ ...current, [extension]: "saved" }));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await loadRoutes();
        setMessages((current) => ({ ...current, [extension]: "stale" }));
      } else if (error instanceof ApiError && error.status === 401) {
        setStatus("login");
        setCsrfToken(null);
      } else setMessages((current) => ({ ...current, [extension]: "error" }));
    } finally {
      setSaving(null);
    }
  }

  async function savePhone(device: PhoneDevice) {
    if (!phonebook || (authRequired && !csrfToken)) return;
    const draft = phoneDrafts[device.mac] ?? { label: device.label, extension: device.extension ?? "" };
    setPhoneSaving(device.mac);
    setPhoneMessages((current) => ({ ...current, [device.mac]: "" }));
    try {
      const next = await api.updatePhone(device.mac, draft.label, draft.extension, phonebook.revision, csrfToken ?? "");
      setPhonebook(next);
      setPhoneDrafts(Object.fromEntries(next.devices.map((item) => [item.mac, { label: item.label, extension: item.extension ?? "" }])));
      setPhoneMessages((current) => ({ ...current, [device.mac]: t.assignmentSaved }));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await loadRoutes();
        setPhoneMessages((current) => ({ ...current, [device.mac]: t.stalePhonebook }));
      } else if (error instanceof ApiError && error.status === 401) {
        setStatus("login");
        setCsrfToken(null);
      } else {
        setPhoneMessages((current) => ({ ...current, [device.mac]: t.mappingError }));
      }
    } finally {
      setPhoneSaving(null);
    }
  }

  async function addPhone(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!phonebook || (authRequired && !csrfToken)) return;
    setAddingPhone(true);
    try {
      const next = await api.addPhone({ mac: newPhone.mac, label: newPhone.label, extension: newPhone.extension }, phonebook.revision, csrfToken ?? "");
      setPhonebook(next);
      setPhoneDrafts(Object.fromEntries(next.devices.map((item) => [item.mac, { label: item.label, extension: item.extension ?? "" }])));
      setNewPhone({ mac: "", label: "", extension: "" });
      setPhoneMessages((current) => ({ ...current, form: "" }));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await loadRoutes();
        setPhoneMessages((current) => ({ ...current, form: t.stalePhonebook }));
      } else if (error instanceof ApiError && error.status === 401) {
        setStatus("login");
        setCsrfToken(null);
      } else {
        setPhoneMessages((current) => ({ ...current, form: t.mappingError }));
      }
    } finally {
      setAddingPhone(false);
    }
  }

  if (status === "loading")
    return (
      <div className="center-state" role="status">
        {t.loading}
      </div>
    );
  if (status === "unavailable")
    return (
      <div className="center-state error-state" role="alert">
        {t.unavailable}
        <button className="button secondary" onClick={() => void loadRoutes()}>
          {language === "en" ? "Retry" : "Повторить"}
        </button>
      </div>
    );
  if (status === "login")
    return (
      <main className="login-page">
        <div className="login-card">
          <div className="brand-mark" aria-hidden="true">
            V
          </div>
          <p className="eyebrow">VOICE CONTROL</p>
          <h1>{t.signIn}</h1>
          <p className="muted">{t.loginHint}</p>
          <form onSubmit={signIn}>
            <label htmlFor="admin-password">{t.password}</label>
            <input
              id="admin-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
            {loginError && (
              <p className="field-error" role="alert">
                {t.badPassword}
              </p>
            )}
            <button className="button primary full-width" type="submit">
              {t.signIn}
            </button>
          </form>
          <LanguageControl language={language} onChange={setLanguage} />
        </div>
      </main>
    );

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : "sidebar-expanded"}`}>
      <aside className="sidebar">
        <div className="sidebar-header">
          <a className="brand" href="/admin/" aria-label="Voice Control home">
            <span>Voice<span className="brand-light"> Control</span></span>
          </a>
          <button
            className="sidebar-toggle"
            type="button"
            aria-label={sidebarCollapsed ? t.expandSidebar : t.collapseSidebar}
            aria-expanded={!sidebarCollapsed}
            title={sidebarCollapsed ? t.expandSidebar : t.collapseSidebar}
            onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d={sidebarCollapsed ? "m9 18 6-6-6-6" : "m15 18-6-6 6-6"} />
            </svg>
          </button>
        </div>
        <p className="nav-label">
          {language === "en" ? "MANAGE" : "УПРАВЛЕНИЕ"}
        </p>
        <button className={`nav-item ${page === "phones" ? "active" : ""}`} aria-label={t.phones} title={sidebarCollapsed ? t.phones : undefined} onClick={() => setPage("phones")}>
          <span className="phone-icon" aria-hidden="true">
            ▣
          </span>
          <span className="nav-item-label">{t.phones}</span>
        </button>
        <button className={`nav-item ${page === "existing" ? "active" : ""}`} aria-label={t.existingPhones} title={sidebarCollapsed ? t.existingPhones : undefined} onClick={() => setPage("existing")}>
          <span aria-hidden="true">▤</span><span className="nav-item-label">{t.existingPhones}</span>
        </button>
        <button className={`nav-item ${page === "unassigned" ? "active" : ""}`} aria-label={t.newUnassigned} title={sidebarCollapsed ? t.newUnassigned : undefined} onClick={() => setPage("unassigned")}>
          <span aria-hidden="true">＋</span><span className="nav-item-label">{t.newUnassigned}</span>
        </button>
        <button className={`nav-item ${page === "load" ? "active" : ""}`} aria-label={t.load} title={sidebarCollapsed ? t.load : undefined} onClick={() => setPage("load")}>
          <span aria-hidden="true">◴</span><span className="nav-item-label">{t.load}</span>
        </button>
        <button className={`nav-item ${page === "profiles" ? "active" : ""}`} aria-label={t.voiceProfiles} title={sidebarCollapsed ? t.voiceProfiles : undefined} onClick={() => setPage("profiles")}>
          <span aria-hidden="true">♫</span><span className="nav-item-label">{t.voiceProfiles}</span>
        </button>
        <div className="sidebar-bottom">
          <span className="online-dot" />
          <span className="sidebar-bottom-label">{t.service}</span>
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            {page === "phones" ? t.phones : page === "existing" ? t.existingPhones : page === "unassigned" ? t.newUnassigned : page === "profiles" ? t.voiceProfiles : t.load}
            <span>/</span>
            <strong>{page === "existing" || page === "unassigned" ? t.physicalTitle : page === "phones" ? t.physicalTitle : page === "profiles" ? t.title : t.load}</strong>
          </div>
          <div className="top-actions">
            <span className="service-badge">
              <span className="online-dot" />
              {t.service}
            </span>
            <LanguageControl language={language} onChange={setLanguage} />
            {authRequired && (
              <button className="text-button" onClick={() => void signOut()}>
                {t.signOut}
              </button>
            )}
          </div>
        </header>
        <section className="page-content">
          {page === "load" ? (
            <LoadPage metrics={metrics} language={language} labels={t} />
          ) : page === "existing" || page === "unassigned" || page === "phones" ? (
            <PhysicalPhonesPage
              view={page === "phones" ? "all" : page}
              inventory={phonebook}
              routes={snapshot}
              drafts={phoneDrafts}
              messages={phoneMessages}
              saving={phoneSaving}
              newPhone={newPhone}
              adding={addingPhone}
              labels={t}
              setDraft={(mac, value) => setPhoneDrafts((current) => ({ ...current, [mac]: value }))}
              setNewPhone={setNewPhone}
              onSave={savePhone}
              onAdd={addPhone}
              onNavigate={setPage}
              metrics={metrics}
              language={language}
            />
          ) : <>
          <div className="page-heading">
            <div>
              <p className="eyebrow">{t.phones.toUpperCase()}</p>
              <h1>{t.title}</h1>
              <p className="muted">{t.subtitle}</p>
            </div>
            <span className="count-badge">
              {Object.keys(snapshot?.extensions ?? {}).length}{" "}
              {t.phones.toLowerCase()}
            </span>
          </div>
          <section className="panel" aria-label={t.title}>
            <div className="panel-heading">
              <div>
                <h2>{t.phones}</h2>
                <p className="muted">
                  {language === "en"
                    ? "Roles apply to calls involving each phone."
                    : "Профиль применяется ко всем звонкам с этим телефоном."}
                </p>
              </div>
              <span className="private-label">
                <span className="lock-icon" aria-hidden="true">
                  ◆
                </span>
                {language === "en" ? "Private" : "Приватно"}
              </span>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>{t.phone}</th>
                    <th>{t.role}</th>
                    <th>
                      <span className="sr-only">{t.save}</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(snapshot?.extensions ?? {})
                    .sort(([a], [b]) =>
                      a.localeCompare(b, undefined, { numeric: true }),
                    )
                    .map(([extension]) => (
                      <tr key={extension}>
                        <td>
                          <div className="phone-cell">
                            <span className="phone-avatar" aria-hidden="true">
                              ▣
                            </span>
                            <span className="phone-number">{extension}</span>
                          </div>
                        </td>
                        <td>
                          <label
                            className="sr-only"
                            htmlFor={`profile-${extension}`}
                          >
                            {language === "en"
                              ? `Profile for ${extension}`
                              : `Профиль для ${extension}`}
                          </label>
                          <select
                            id={`profile-${extension}`}
                            value={drafts[extension] ?? "original"}
                            onChange={(event) => {
                              setDrafts((current) => ({
                                ...current,
                                [extension]: event.target.value as Profile,
                              }));
                              setMessages((current) => ({
                                ...current,
                                [extension]: undefined,
                              }));
                            }}
                          >
                            <option value="original">{t.original}</option>
                            <option value="phone-guy">{t.phoneGuy}</option>
                          </select>
                        </td>
                        <td className="row-actions">
                          <span
                            className={`save-message ${messages[extension] ?? ""}`}
                            role={messages[extension] ? "status" : undefined}
                          >
                            {messages[extension] === "saved"
                              ? t.saved
                              : messages[extension] === "stale"
                                ? t.stale
                                : messages[extension] === "error"
                                  ? t.saveError
                                  : ""}
                          </span>
                          <button
                            className="button primary save-button"
                            disabled={
                              saving === extension ||
                              drafts[extension] ===
                                snapshot?.extensions[extension]
                            }
                            onClick={() => void save(extension)}
                          >
                            {saving === extension
                              ? "…"
                              : `${t.save} ${extension}`}
                          </button>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            <div className="panel-footer">
              <span className="online-dot" />
              {language === "en"
                ? `Configuration revision ${snapshot?.revision}`
                : `Версия конфигурации ${snapshot?.revision}`}
            </div>
          </section>
          <section className="panel browser-phone-admin" aria-label={t.browserPhones}>
            <div className="panel-heading">
              <div><h2>{t.browserPhones}</h2><p className="muted">{language === "en" ? "Live browser registrations on internal extensions." : "Текущие регистрации браузеров на внутренних номерах."}</p></div>
              <a className="text-button" href="/phone/">{t.openPhone}</a>
            </div>
            {browserPhones.length ? <div className="browser-phone-list">{browserPhones.map((phone)=><div className="browser-phone-row" key={phone.extension}><span className="online-dot"/><strong>{phone.nickname}</strong><span>{phone.extension}</span><span className="browser-connected-label">{t.browserConnected}</span></div>)}</div> : <p className="browser-phone-empty">{t.noBrowserPhones}</p>}
          </section>
          </>}
        </section>
      </main>
    </div>
  );
}

function PhysicalPhonesPage({
  view,
  inventory,
  routes,
  drafts,
  messages,
  saving,
  newPhone,
  adding,
  labels,
  setDraft,
  setNewPhone,
  onSave,
  onAdd,
  onNavigate,
  metrics,
  language,
}: {
  view: "all" | "existing" | "unassigned";
  inventory: PhonebookSnapshot | null;
  routes: RouteSnapshot | null;
  drafts: Record<string, { label: string; extension: string }>;
  messages: Record<string, string>;
  saving: string | null;
  newPhone: { mac: string; label: string; extension: string };
  adding: boolean;
  labels: Record<string, string>;
  setDraft: (mac: string, value: { label: string; extension: string }) => void;
  setNewPhone: (value: { mac: string; label: string; extension: string }) => void;
  onSave: (device: PhoneDevice) => void;
  onAdd: (event: FormEvent<HTMLFormElement>) => void;
  onNavigate: (page: "phones" | "existing" | "unassigned" | "profiles" | "load") => void;
  metrics: MetricsSnapshot | null;
  language: Language;
}) {
  const allDevices = inventory?.devices ?? [];
  const assignedDevices = allDevices.filter((device) => Boolean(device.extension));
  const unassignedDevices = allDevices.filter((device) => !device.extension);
  const devices = view === "all" ? allDevices : allDevices.filter((device) => view === "existing" ? Boolean(device.extension) : !device.extension);
  const extensions = Object.keys(routes?.extensions ?? {}).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  if (view === "all") return <>
    <div className="page-heading">
      <div><p className="eyebrow">{labels.phones.toUpperCase()}</p><h1>{labels.physicalPhones}</h1><p className="muted">{language === "en" ? "Assign SIP extensions to the physical phones connected to your service." : "Назначайте внутренние SIP-номера физическим телефонам, подключённым к сервису."}</p></div>
      <span className="count-badge">{allDevices.length} {labels.physicalPhones.toLowerCase()}</span>
    </div>
    <section className="panel inventory-panel" aria-label={labels.assignedHeading}>
      <div className="panel-heading"><div><h2>{labels.assignedHeading}</h2><p className="muted">{labels.mappingOnly}</p></div><button className="button primary" type="button" onClick={() => onNavigate("unassigned")}>＋ {labels.addDevice}</button></div>
      <div className="table-scroll"><table className="inventory-table">
        <thead><tr><th>{labels.device}</th><th>{labels.mac}</th><th>IP</th><th>{labels.phone}</th><th>{labels.status}</th><th><span className="sr-only">{labels.save}</span></th></tr></thead>
        <tbody>{assignedDevices.map((device) => <PhoneInventoryRow key={device.mac} device={device} allDevices={allDevices} extensions={extensions} draft={drafts[device.mac] ?? { label: device.label, extension: device.extension ?? "" }} message={messages[device.mac]} saving={saving === device.mac} labels={labels} setDraft={(value) => setDraft(device.mac, value)} onSave={() => void onSave(device)} />)}
          {assignedDevices.length === 0 && <tr><td colSpan={6} className="inventory-empty">{labels.noPhones}</td></tr>}
        </tbody>
      </table></div>
      <div className="panel-footer"><span>{labels.revision}: {inventory?.revision ?? "—"}</span></div>
    </section>
    <section className="panel inventory-panel unassigned-panel" aria-label={labels.unassignedHeading}>
      <div className="panel-heading"><div><h2>{labels.unassignedHeading}</h2><p className="muted">{language === "en" ? "Phones listed here do not have an extension assigned yet." : "Здесь собраны телефоны, которым ещё не назначен внутренний номер."}</p></div><button className="button secondary" type="button" onClick={() => onNavigate("unassigned")}>＋ {labels.addDevice}</button></div>
      {unassignedDevices.length ? <div className="table-scroll"><table className="inventory-table"><thead><tr><th>{labels.device}</th><th>{labels.mac}</th><th>IP</th><th>{labels.status}</th></tr></thead><tbody>{unassignedDevices.map((device) => <tr key={device.mac}><td><strong>{device.label}</strong></td><td className="device-mac">{device.mac}</td><td>{device.lastSeenIp ?? labels.unknown}</td><td><span className={`observation-pill ${device.lastSeenAt ? "observed" : "unknown"}`}><span />{device.lastSeenAt ? labels.historyAvailable : labels.historyMissing}</span></td></tr>)}</tbody></table></div> : <div className="inventory-empty-state"><span className="empty-phone-icon" aria-hidden="true">▯</span><strong>{labels.noUnassigned}</strong><p>{language === "en" ? "New phones will appear here when they are added to the inventory." : "Новые телефоны появятся здесь после добавления в реестр."}</p></div>}
    </section>
    <section className="panel compact-load" aria-label={labels.systemLoad}>
      <div className="compact-load-heading"><div><h2>{labels.systemLoad}</h2><p className="muted">{labels.loadSummary}</p></div><button className="text-button" type="button" onClick={() => onNavigate("load")}>{language === "en" ? "Details →" : "Подробнее →"}</button></div>
      <div className="compact-load-grid">
        <div className="compact-load-stat"><span className={`online-dot ${metrics?.rvc.status === "ready" ? "" : "offline"}`} /><div><strong>{metrics?.rvc.status === "ready" ? (language === "en" ? "RVC ready" : "RVC готов") : labels.unavailableMetric}</strong><small>{labels.worker}</small></div></div>
        <div className="compact-load-stat"><span className="load-stat-icon" aria-hidden="true">☎</span><div><strong>{metrics?.calls.active ?? "—"}</strong><small>{labels.callsActive}</small></div></div>
        <div className="compact-load-stat"><span className="load-stat-icon" aria-hidden="true">▥</span><div><strong>{metrics?.calls.limit ?? "—"}</strong><small>{language === "en" ? "Concurrent call limit" : "Одновременных звонков"}</small></div></div>
      </div>
    </section>
  </>;
  return <>
    <div className="page-heading">
      <div><p className="eyebrow">{view === "existing" ? labels.existingPhones.toUpperCase() : labels.newUnassigned.toUpperCase()}</p><h1>{view === "existing" ? labels.existingPhones : labels.newUnassigned}</h1><p className="muted">{view === "unassigned" ? labels.manualInventory : labels.mappingOnly}</p></div>
      <span className="count-badge">{devices.length} {labels.physicalPhones.toLowerCase()}</span>
    </div>
    <section className="panel" aria-label={labels.physicalTitle}>
      <div className="panel-heading"><div><h2>{view === "existing" ? labels.existingPhones : labels.newUnassigned}</h2><p className="muted">{labels.lastObserved}: {devices.some((device) => device.lastSeenAt) ? [...devices].map((device) => device.lastSeenAt).filter(Boolean).join(", ") : labels.unknown}</p></div><span className="private-label">{devices.length}</span></div>
      <div className="table-scroll"><table>
        <thead><tr><th>{labels.label} / {labels.mac}</th><th>{labels.lastAddress}</th><th>{labels.phone}</th><th><span className="sr-only">{labels.save}</span></th></tr></thead>
        <tbody>
          {devices.map((device) => {
            const draft = drafts[device.mac] ?? { label: device.label, extension: device.extension ?? "" };
            const occupied = new Set(allDevices.filter((other) => other.mac !== device.mac).map((other) => other.extension).filter(Boolean));
            const changed = draft.label !== device.label || draft.extension !== (device.extension ?? "");
            return <tr key={device.mac}>
              <td><label className="sr-only" htmlFor={`label-${device.mac}`}>{labels.label} {device.mac}</label><input id={`label-${device.mac}`} value={draft.label} maxLength={80} onChange={(event) => setDraft(device.mac, { ...draft, label: event.target.value })} /><div className="muted device-mac">{device.mac}</div><div className="muted device-observation">{device.observation ?? labels.unknown}</div></td>
              <td>{device.lastSeenIp ?? labels.unknown}<div className="muted device-observation">{device.lastSeenAt ?? labels.unknown}</div></td>
              <td><label className="sr-only" htmlFor={`extension-${device.mac}`}>{labels.phone} {device.mac}</label><select id={`extension-${device.mac}`} value={draft.extension} onChange={(event) => setDraft(device.mac, { ...draft, extension: event.target.value })}><option value="">{labels.unassigned}</option>{extensions.map((extension) => <option key={extension} value={extension} disabled={occupied.has(extension)}>{extension}</option>)}</select></td>
              <td className="row-actions"><span className="save-message saved" role={messages[device.mac] ? "status" : undefined}>{messages[device.mac]}</span><button className="button primary save-button" disabled={!changed || saving === device.mac} onClick={() => void onSave(device)}>{saving === device.mac ? "…" : labels.save}</button></td>
            </tr>;
          })}
          {devices.length === 0 && <tr><td colSpan={4} className="muted">{view === "existing" ? labels.noPhones : labels.noUnassigned}</td></tr>}
        </tbody>
      </table></div>
      <div className="panel-footer"><span>{labels.revision}: {inventory?.revision ?? "—"}</span></div>
    </section>
    {view === "unassigned" && <section className="panel phone-add-panel">
      <div className="panel-heading"><div><h2>{labels.addDevice}</h2><p className="muted">{labels.mappingOnly}</p></div></div>
      <form className="phone-add-form" onSubmit={onAdd}>
        <label>{labels.mac}<input required value={newPhone.mac} onChange={(event) => setNewPhone({ ...newPhone, mac: event.target.value })} placeholder="00:11:22:33:44:55" /></label>
        <label>{labels.label}<input required maxLength={80} value={newPhone.label} onChange={(event) => setNewPhone({ ...newPhone, label: event.target.value })} /></label>
        <label>{labels.phone}<select value={newPhone.extension} onChange={(event) => setNewPhone({ ...newPhone, extension: event.target.value })}><option value="">{labels.unassigned}</option>{extensions.map((extension) => <option key={extension} value={extension} disabled={allDevices.some((device) => device.extension === extension)}>{extension}</option>)}</select></label>
        <button className="button primary" type="submit" disabled={adding}>{adding ? "…" : labels.addDevice}</button>
      </form>
      {messages.form && <p className="field-error" role="alert">{messages.form}</p>}
    </section>}
  </>;
}

function PhoneInventoryRow({ device, allDevices, extensions, draft, message, saving, labels, setDraft, onSave }: {
  device: PhoneDevice;
  allDevices: PhoneDevice[];
  extensions: string[];
  draft: { label: string; extension: string };
  message?: string;
  saving: boolean;
  labels: Record<string, string>;
  setDraft: (value: { label: string; extension: string }) => void;
  onSave: () => void;
}) {
  const occupied = new Set(allDevices.filter((other) => other.mac !== device.mac).map((other) => other.extension).filter(Boolean));
  const changed = draft.label !== device.label || draft.extension !== (device.extension ?? "");
  return <tr>
    <td><label className="sr-only" htmlFor={`label-${device.mac}`}>{labels.label} {device.mac}</label><input className="device-label-input" id={`label-${device.mac}`} value={draft.label} maxLength={80} onChange={(event) => setDraft({ ...draft, label: event.target.value })} /></td>
    <td className="device-mac">{device.mac}</td><td>{device.lastSeenIp ?? labels.unknown}</td>
    <td><label className="sr-only" htmlFor={`extension-${device.mac}`}>{labels.phone} {device.mac}</label><select id={`extension-${device.mac}`} value={draft.extension} onChange={(event) => setDraft({ ...draft, extension: event.target.value })}><option value="">{labels.unassigned}</option>{extensions.map((extension) => <option key={extension} value={extension} disabled={occupied.has(extension)}>{extension}</option>)}</select></td>
    <td><span className={`observation-pill ${device.lastSeenAt ? "observed" : "unknown"}`} title={device.lastSeenAt ?? labels.unknown}><span />{device.lastSeenAt ? labels.historyAvailable : labels.historyMissing}</span></td>
    <td className="row-actions"><span className="save-message saved" role={message ? "status" : undefined}>{message}</span><button className="button primary save-button" disabled={!changed || saving} onClick={onSave}>{saving ? "…" : labels.save}</button></td>
  </tr>;
}

function LoadPage({ metrics, language, labels }: { metrics: MetricsSnapshot | null; language: Language; labels: Record<string, string> }) {
  const freshness = metrics?.rvc.fresh ? (language === "en" ? "Fresh" : "Свежие") : metrics ? labels.staleMetric : labels.unavailableMetric;
  const latency = metrics?.processing.samples ? `${labels.p50}: ${metrics.processing.p50Millis?.toFixed(1) ?? "—"} ms · ${labels.p95}: ${metrics.processing.p95Millis?.toFixed(1) ?? "—"} ms` : labels.notMeasured;
  const hostAvailable = metrics?.host.status === "ready" || metrics?.host.status === "partial";
  return <>
    <div className="page-heading"><div><p className="eyebrow">{labels.load.toUpperCase()}</p><h1>{labels.load}</h1><p className="muted">{language === "en" ? "Live service state and measured processing load." : "Текущее состояние сервиса и измеренная нагрузка."}</p></div></div>
    <div className="metrics-grid">
      <section className="panel metric-card"><p className="muted">{labels.allowedNow}</p><strong>{metrics?.calls.limit ?? 1}</strong><span>{language === "en" ? "concurrent processed calls" : "одновременный обработанный звонок"}</span></section>
      <section className="panel metric-card"><p className="muted">{labels.estimate}</p><strong>{labels.notMeasured}</strong><span>{language === "en" ? "isolated capacity benchmark required" : "нужно изолированное измерение мощности"}</span></section>
      <section className="panel metric-card"><p className="muted">{labels.worker}</p><strong>{metrics?.rvc.status ?? labels.unavailableMetric}</strong><span>{freshness}{metrics?.rvc.lastSuccessAt ? ` · ${new Date(metrics.rvc.lastSuccessAt).toLocaleTimeString()}` : ""}</span></section>
      <section className="panel metric-card"><p className="muted">{labels.callsActive}</p><strong>{metrics?.calls.active ?? "—"} / {metrics?.calls.limit ?? 1}</strong><span>{labels.queue}: {metrics?.rvc.queuedWindows ?? "—"}</span></section>
      <section className="panel metric-card"><p className="muted">{labels.latency}</p><strong className="metric-latency">{latency}</strong><span>{metrics?.processing.samples ?? 0} {language === "en" ? "samples in rolling window" : "замеров в скользящем окне"}</span></section>
      <section className="panel metric-card"><p className="muted">{language === "en" ? "VM CPU / RAM" : "CPU / RAM виртуальной машины"}</p><strong>{hostAvailable && metrics?.host.cpuReady ? `${metrics.host.cpuPercent?.toFixed(1) ?? "—"}% CPU` : hostAvailable ? (language === "en" ? "Sampling…" : "Собираем данные…") : labels.unavailableMetric}</strong><span>{hostAvailable ? `${formatBytes(metrics?.host.memoryUsedBytes ?? 0)} / ${formatBytes(metrics?.host.memoryTotalBytes ?? 0)} RAM` : metrics?.host.status === "warming" ? (language === "en" ? "Sampling…" : "Собираем данные…") : labels.unavailableMetric}</span></section>
      <section className="panel metric-card"><p className="muted">{language === "en" ? "RVC worker CPU / RAM" : "CPU / RAM RVC-обработчика"}</p><strong>{hostAvailable && metrics?.host.rvcCpuReady ? `${metrics.host.rvcCpuPercent?.toFixed(1) ?? "—"}% CPU` : hostAvailable ? (language === "en" ? "Sampling…" : "Собираем данные…") : labels.unavailableMetric}</strong><span>{hostAvailable && metrics?.host.rvcMemoryLimitBytes ? `${formatBytes(metrics.host.rvcMemoryBytes ?? 0)} / ${formatBytes(metrics.host.rvcMemoryLimitBytes)} RAM` : metrics?.host.error ?? labels.unavailableMetric}</span></section>
      <section className="panel metric-card"><p className="muted">GPU / VRAM</p><strong>{labels.unavailableMetric}</strong><span>{language === "en" ? "No restricted live GPU metrics source" : "Нет ограниченного источника метрик GPU"}</span></section>
    </div>
    <p className="muted metrics-updated">{labels.updated}: {metrics ? new Date(metrics.updatedAt).toLocaleTimeString() : "—"}</p>
  </>;
}

function formatBytes(value: number) {
  if (!value) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** unit).toFixed(unit > 1 ? 1 : 0)} ${units[unit]}`;
}

function LanguageControl({
  language,
  onChange,
}: {
  language: Language;
  onChange: (language: Language) => void;
}) {
  return (
    <div className="language-control" aria-label="Language">
      <button aria-pressed={language === "en"} onClick={() => onChange("en")}>
        EN
      </button>
      <span aria-hidden="true">·</span>
      <button aria-pressed={language === "ru"} onClick={() => onChange("ru")}>
        RU
      </button>
    </div>
  );
}
