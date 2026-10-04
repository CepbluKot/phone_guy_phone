import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, AsteriskSnapshot, BrowserPhone, PhoneDevice, PhonebookSnapshot, Profile, RouteSnapshot } from "./api";
import { VoiceShell, type VoicePage } from "./components/VoiceShell";
import "./design-system.css";
import "./styles.css";

type Language = "en" | "ru";
const copy = {
  en: {
    appName: "Voice Control",
    manage: "Manage",
    adminHome: "Admin",
    browserPhone: "Browser phone",
    signIn: "Sign in",
    password: "Admin password",
    loginHint: "Private phone settings",
    loading: "Loading phone profiles…",
    unavailable: "Service is unavailable. Try again in a moment.",
    badPassword: "Password is incorrect.",
    title: "Phone profiles",
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
    asterisk: "Asterisk",
    asteriskTitle: "Asterisk status",
    asteriskSummary: "Live SIP registration and call state, refreshed every five seconds.",
    activeChannels: "Active channels",
    online: "Online",
    offline: "Offline",
    statusUnavailable: "Status unavailable",
    physicalPhones: "Physical phones",
    profilePhysicalPhones: "Physical phones",
    profilePhysicalSummary: "Voice profiles for phones assigned in the physical device register.",
    virtualPhones: "Virtual numbers",
    virtualPhoneSummary: "Configured numbers without a physical phone or an active browser session.",
    noAssignedProfilePhones: "No physical phones with an assigned internal number.",
    noVirtualPhones: "No virtual numbers.",
    physicalPhoneCount: "Physical phones",
    virtualPhoneCount: "Virtual numbers",
    existingPhones: "Existing phones",
    newUnassigned: "New / unassigned",
    assignedHeading: "Assigned phones",
    unassignedHeading: "Phones without a number",
    unassignedListHeading: "Awaiting assignment",
    unassignedSummary: "Known physical phones without an assigned internal number.",
    unassignedInstruction: "Choose a free SIP extension and save the mapping.",
    unassignedCount: "without a number",
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
    noUnassigned: "No known physical phones are waiting for a number.",
    manualInventory: "These records are entered manually; automatic network discovery is not configured.",
    signOut: "Sign out",
    collapseSidebar: "Collapse sidebar",
    expandSidebar: "Expand sidebar",
    openMobileNavigation: "Open menu",
    closeMobileNavigation: "Close menu",
    dismissMobileNavigation: "Close navigation drawer",
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
    browserRole: "Voice profile",
    openPhone: "Open browser phone →",
  },
  ru: {
    appName: "Voice Control",
    manage: "Управление",
    adminHome: "Админка",
    browserPhone: "Телефон в браузере",
    signIn: "Войти",
    password: "Пароль администратора",
    loginHint: "Приватные настройки телефонов",
    loading: "Загружаем профили телефонов…",
    unavailable: "Сервис недоступен. Попробуйте чуть позже.",
    badPassword: "Неверный пароль.",
    title: "Профили телефонов",
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
    asterisk: "Asterisk",
    asteriskTitle: "Статус Asterisk",
    asteriskSummary: "Текущая регистрация SIP и состояние звонков. Обновление каждые пять секунд.",
    activeChannels: "Активные каналы",
    online: "В сети",
    offline: "Не в сети",
    statusUnavailable: "Статус недоступен",
    physicalPhones: "Физические телефоны",
    profilePhysicalPhones: "Физические телефоны",
    profilePhysicalSummary: "Профили голосов для аппаратов из реестра физических телефонов.",
    virtualPhones: "Виртуальные номера",
    virtualPhoneSummary: "Настроенные номера без физического аппарата и активной браузерной сессии.",
    noAssignedProfilePhones: "Нет физических телефонов с назначенным внутренним номером.",
    noVirtualPhones: "Виртуальных номеров нет.",
    physicalPhoneCount: "Физических телефонов",
    virtualPhoneCount: "Виртуальных номеров",
    existingPhones: "Назначенные телефоны",
    newUnassigned: "Новые / без номера",
    assignedHeading: "Назначенные телефоны",
    unassignedHeading: "Телефоны без номера",
    unassignedListHeading: "Ожидают назначения",
    unassignedSummary: "Известные физические телефоны, которым ещё не назначен внутренний номер.",
    unassignedInstruction: "Выберите свободный внутренний номер SIP и сохраните привязку.",
    unassignedCount: "без номера",
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
    noUnassigned: "Нет известных физических телефонов, ожидающих назначения номера.",
    manualInventory: "Эти записи добавляются вручную; автоматическое обнаружение в сети не настроено.",
    signOut: "Выйти",
    collapseSidebar: "Свернуть меню",
    expandSidebar: "Развернуть меню",
    openMobileNavigation: "Открыть меню",
    closeMobileNavigation: "Закрыть меню",
    dismissMobileNavigation: "Закрыть панель навигации",
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
    browserRole: "Профиль голоса",
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
  const [asterisk, setAsterisk] = useState<AsteriskSnapshot | null>(null);
  const [browserPhones, setBrowserPhones] = useState<BrowserPhone[]>([]);
  const [browserDrafts, setBrowserDrafts] = useState<Record<string, Profile>>({});
  const [browserMessages, setBrowserMessages] = useState<Record<string, string>>({});
  const [browserSaving, setBrowserSaving] = useState<string | null>(null);
  const [page, setPage] = useState<VoicePage>(() => pageFromHash(window.location.hash));
  const [phonebook, setPhonebook] = useState<PhonebookSnapshot | null>(null);
  const [phoneDrafts, setPhoneDrafts] = useState<Record<string, { label: string; extension: string }>>({});
  const [phoneMessages, setPhoneMessages] = useState<Record<string, string>>({});
  const [phoneSaving, setPhoneSaving] = useState<string | null>(null);
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

  const navigateToPage = useCallback((nextPage: VoicePage) => {
    const nextHash = `#/${nextPage}`;
    if (window.location.hash !== nextHash) window.history.pushState(null, "", nextHash);
    setPage(nextPage);
  }, []);

  useEffect(() => {
    const syncPageFromLocation = () => setPage(pageFromHash(window.location.hash));
    window.addEventListener("popstate", syncPageFromLocation);
    window.addEventListener("hashchange", syncPageFromLocation);
    return () => {
      window.removeEventListener("popstate", syncPageFromLocation);
      window.removeEventListener("hashchange", syncPageFromLocation);
    };
  }, []);

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
      try {
        const value = await api.asterisk();
        if (!disposed) setAsterisk(value);
      } catch {
        if (!disposed) setAsterisk(null);
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [status]);

  useEffect(() => {
    if (status !== "ready") return;
    let disposed = false;
    const refresh = async () => { try { const value = await api.browserPhones(); if (!disposed) { const sessions = Array.isArray(value.sessions) ? value.sessions : []; setBrowserPhones(sessions); setBrowserDrafts((current) => Object.fromEntries(sessions.map((phone) => [phone.extension, current[phone.extension] ?? snapshot?.browserExtensions?.[phone.extension] ?? "original"]))); } } catch { if (!disposed) setBrowserPhones([]); } };
    void refresh(); const timer=window.setInterval(()=>void refresh(),5000);return()=>{disposed=true;window.clearInterval(timer)};
  }, [status, snapshot]);

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

  async function saveBrowser(extension: string) {
    if (!snapshot || (authRequired && !csrfToken)) return;
    setBrowserSaving(extension);
    setBrowserMessages((current) => ({ ...current, [extension]: "" }));
    try {
      const next = await api.updateBrowser(extension, browserDrafts[extension] ?? "original", snapshot.revision, csrfToken ?? "");
      setSnapshot(next);
      setBrowserDrafts((current) => ({ ...current, [extension]: next.browserExtensions?.[extension] ?? "original" }));
      setBrowserMessages((current) => ({ ...current, [extension]: t.saved }));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        await loadRoutes();
        setBrowserMessages((current) => ({ ...current, [extension]: t.stale }));
      } else if (error instanceof ApiError && error.status === 401) {
        setStatus("login"); setCsrfToken(null);
      } else setBrowserMessages((current) => ({ ...current, [extension]: t.saveError }));
    } finally { setBrowserSaving(null); }
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

  const profilePhysicalPhones = (phonebook?.devices ?? [])
    .filter((device) => Boolean(device.extension))
    .sort((a, b) => (a.extension ?? "").localeCompare(b.extension ?? "", undefined, { numeric: true }));
  const physicalExtensions = new Set(profilePhysicalPhones.map((device) => device.extension!));
  const activeBrowserExtensions = new Set(browserPhones.map((phone) => phone.extension));
  const virtualExtensions = Object.keys(snapshot?.extensions ?? {})
    .filter((extension) => !physicalExtensions.has(extension) && !activeBrowserExtensions.has(extension))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

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
      <VoiceShell
        page={page}
        labels={t}
        sidebarCollapsed={sidebarCollapsed}
        onSidebarCollapsedChange={setSidebarCollapsed}
        onNavigate={navigateToPage}
        onSignOut={authRequired ? () => void signOut() : undefined}
      >
        <section className="page-content">
          {page === "phones" ? (
            <PhysicalPhonesPage
              inventory={phonebook}
              routes={snapshot}
              drafts={phoneDrafts}
              messages={phoneMessages}
              saving={phoneSaving}
              labels={t}
              setDraft={(mac, value) => setPhoneDrafts((current) => ({ ...current, [mac]: value }))}
              onSave={savePhone}
            />
          ) : page === "asterisk" ? <AsteriskPage value={asterisk} labels={t} language={language} /> : <>
            <div className="page-heading">
            <div>
              <p className="eyebrow">{t.phones.toUpperCase()}</p>
              <h1>{t.title}</h1>
              <p className="muted">{language === "en" ? "Physical phones, browser phones, and virtual numbers are shown separately." : "Физические телефоны, браузерные телефоны и виртуальные номера показаны отдельно."}</p>
            </div>
            <span className="count-badge">
              {t.physicalPhoneCount}: {profilePhysicalPhones.length}
            </span>
          </div>
          <section className="panel" aria-label={t.profilePhysicalPhones}>
            <div className="panel-heading">
              <div>
                <h2>{t.profilePhysicalPhones}</h2>
                <p className="muted">{t.profilePhysicalSummary}</p>
              </div>
              <span className="private-label">
                <span className="lock-icon" aria-hidden="true">
                  ◆
                </span>
                {language === "en" ? "Private" : "Приватно"}
              </span>
            </div>
            <ProfileTable
              extensions={profilePhysicalPhones.map((device) => device.extension!)}
              phoneLabels={Object.fromEntries(profilePhysicalPhones.map((device) => [device.extension!, device.label]))}
              routes={snapshot}
              drafts={drafts}
              messages={messages}
              saving={saving}
              labels={t}
              language={language}
              onDraftChange={(extension, profile) => {
                setDrafts((current) => ({ ...current, [extension]: profile }));
                setMessages((current) => ({ ...current, [extension]: undefined }));
              }}
              onSave={(extension) => void save(extension)}
              emptyMessage={t.noAssignedProfilePhones}
            />
            <div className="panel-footer">
              <span className="online-dot" />
              {language === "en"
                ? `Configuration revision ${snapshot?.revision}`
                : `Версия конфигурации ${snapshot?.revision}`}
            </div>
          </section>
          <section className="panel virtual-number-admin" aria-label={t.virtualPhones}>
            <div className="panel-heading">
              <div>
                <h2>{t.virtualPhones}</h2>
                <p className="muted">{t.virtualPhoneSummary}</p>
              </div>
              <span className="count-badge">{t.virtualPhoneCount}: {virtualExtensions.length}</span>
            </div>
            <ProfileTable
              extensions={virtualExtensions}
              routes={snapshot}
              drafts={drafts}
              messages={messages}
              saving={saving}
              labels={t}
              language={language}
              onDraftChange={(extension, profile) => {
                setDrafts((current) => ({ ...current, [extension]: profile }));
                setMessages((current) => ({ ...current, [extension]: undefined }));
              }}
              onSave={(extension) => void save(extension)}
              emptyMessage={t.noVirtualPhones}
            />
          </section>
          <section className="panel browser-phone-admin" aria-label={t.browserPhones}>
            <div className="panel-heading">
              <div><h2>{t.browserPhones}</h2><p className="muted">{language === "en" ? "Live browser registrations on internal extensions." : "Текущие регистрации браузеров на внутренних номерах."}</p></div>
              <a className="text-button" href="/phone/">{t.openPhone}</a>
            </div>
            {browserPhones.length ? <div className="browser-phone-list">{browserPhones.map((phone)=>{const profile=browserDrafts[phone.extension]??snapshot?.browserExtensions?.[phone.extension]??"original";return <div className="browser-phone-row" key={phone.extension}><span className="online-dot"/><strong>{phone.nickname}</strong><span>{phone.extension}</span><label className="browser-profile-control"><span className="sr-only">{t.browserRole} · {phone.extension}</span><select aria-label={`${t.browserRole} · ${phone.extension}`} value={profile} onChange={(event)=>setBrowserDrafts((current)=>({...current,[phone.extension]:event.target.value as Profile}))}><option value="original">{t.original}</option><option value="phone-guy">{t.phoneGuy}</option></select><button className="button primary save-button" disabled={browserSaving===phone.extension||profile===(snapshot?.browserExtensions?.[phone.extension]??"original")} onClick={()=>void saveBrowser(phone.extension)}>{browserSaving===phone.extension?"…":t.save}</button></label><span className="browser-connected-label">{browserMessages[phone.extension]||t.browserConnected}</span></div>})}</div> : <p className="browser-phone-empty">{t.noBrowserPhones}</p>}
          </section>
          </>}
        </section>
      </VoiceShell>
  );
}

function ProfileTable({
  extensions,
  phoneLabels = {},
  routes,
  drafts,
  messages,
  saving,
  labels,
  language,
  onDraftChange,
  onSave,
  emptyMessage,
}: {
  extensions: string[];
  phoneLabels?: Record<string, string>;
  routes: RouteSnapshot | null;
  drafts: Record<string, Profile>;
  messages: Partial<Record<string, "saved" | "stale" | "error">>;
  saving: string | null;
  labels: Record<string, string>;
  language: Language;
  onDraftChange: (extension: string, profile: Profile) => void;
  onSave: (extension: string) => void;
  emptyMessage: string;
}) {
  return (
    <div className="table-scroll">
      <table className="voice-profile-table">
        <thead>
          <tr>
            <th>{labels.phone}</th>
            <th>{labels.role}</th>
            <th><span className="sr-only">{labels.save}</span></th>
          </tr>
        </thead>
        <tbody>
          {extensions.map((extension) => (
            <tr className={phoneLabels[extension] ? "physical-profile-row" : "virtual-profile-row"} key={extension}>
              <td data-label={labels.phone}>
                <div className="phone-cell">
                  <span className="phone-avatar" aria-hidden="true">{phoneLabels[extension] ? "▣" : "#"}</span>
                  <span className="profile-phone-identity">
                    <span className="phone-number">{extension}</span>
                    {phoneLabels[extension] && <span className="profile-phone-label">{phoneLabels[extension]}</span>}
                  </span>
                </div>
              </td>
              <td data-label={labels.role}>
                <label className="sr-only" htmlFor={`profile-${extension}`}>
                  {language === "en" ? `Profile for ${extension}` : `Профиль для ${extension}`}
                </label>
                <select
                  id={`profile-${extension}`}
                  value={drafts[extension] ?? "original"}
                  onChange={(event) => onDraftChange(extension, event.target.value as Profile)}
                >
                  <option value="original">{labels.original}</option>
                  <option value="phone-guy">{labels.phoneGuy}</option>
                </select>
              </td>
              <td className="row-actions" data-label="">
                <span className={`save-message ${messages[extension] ?? ""}`} role={messages[extension] ? "status" : undefined}>
                  {messages[extension] === "saved" ? labels.saved : messages[extension] === "stale" ? labels.stale : messages[extension] === "error" ? labels.saveError : ""}
                </span>
                <button
                  className="button primary save-button"
                  disabled={saving === extension || drafts[extension] === routes?.extensions[extension]}
                  onClick={() => onSave(extension)}
                >
                  {saving === extension ? "…" : `${labels.save} ${extension}`}
                </button>
              </td>
            </tr>
          ))}
          {extensions.length === 0 && <tr><td colSpan={3} className="inventory-empty">{emptyMessage}</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function PhysicalPhonesPage({
  inventory,
  routes,
  drafts,
  messages,
  saving,
  labels,
  setDraft,
  onSave,
}: {
  inventory: PhonebookSnapshot | null;
  routes: RouteSnapshot | null;
  drafts: Record<string, { label: string; extension: string }>;
  messages: Record<string, string>;
  saving: string | null;
  labels: Record<string, string>;
  setDraft: (mac: string, value: { label: string; extension: string }) => void;
  onSave: (device: PhoneDevice) => void;
}) {
  const allDevices = inventory?.devices ?? [];
  const unassignedDevices = allDevices.filter((device) => !device.extension);
  const extensions = Object.keys(routes?.extensions ?? {}).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const assignmentComplete = Object.values(messages).includes(labels.assignmentSaved);
  return <>
    <div className="page-heading">
      <div><p className="eyebrow">{labels.phones.toUpperCase()}</p><h1>{labels.unassignedHeading}</h1><p className="muted">{labels.unassignedSummary}</p></div>
      <span className="count-badge">{unassignedDevices.length} {labels.unassignedCount}</span>
    </div>
    <section className="panel inventory-panel unassigned-panel" aria-label={labels.unassignedHeading}>
      <div className="panel-heading"><div><h2>{labels.unassignedListHeading}</h2><p className="muted">{labels.unassignedInstruction}</p></div></div>
      {assignmentComplete && <p className="assignment-notice" role="status">{labels.assignmentSaved}</p>}
      {unassignedDevices.length ? <div className="table-scroll"><table className="inventory-table"><thead><tr><th>{labels.device}</th><th>{labels.mac}</th><th>IP</th><th>{labels.phone}</th><th>{labels.status}</th><th><span className="sr-only">{labels.save}</span></th></tr></thead><tbody>{unassignedDevices.map((device) => <PhoneInventoryRow key={device.mac} device={device} allDevices={allDevices} extensions={extensions} draft={drafts[device.mac] ?? { label: device.label, extension: device.extension ?? "" }} message={messages[device.mac]} saving={saving === device.mac} labels={labels} setDraft={(value) => setDraft(device.mac, value)} onSave={() => void onSave(device)} />)}</tbody></table></div> : <div className="inventory-empty-state"><span className="empty-phone-icon" aria-hidden="true">▯</span><strong>{labels.noUnassigned}</strong></div>}
      <div className="panel-footer"><span>{labels.revision}: {inventory?.revision ?? "—"}</span></div>
    </section>
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
  const observed = Boolean(device.lastSeenAt);
  return <tr>
    <td data-label={labels.device}><label className="sr-only" htmlFor={`label-${device.mac}`}>{labels.label} {device.mac}</label><input className="device-label-input" id={`label-${device.mac}`} value={draft.label} maxLength={80} onChange={(event) => setDraft({ ...draft, label: event.target.value })} /></td>
    <td className="device-mac" data-label={labels.mac}>{device.mac}</td><td data-label="IP">{device.lastSeenIp ?? labels.unknown}</td>
    <td data-label={labels.phone}><label className="sr-only" htmlFor={`extension-${device.mac}`}>{labels.phone} {device.mac}</label><select id={`extension-${device.mac}`} value={draft.extension} onChange={(event) => setDraft({ ...draft, extension: event.target.value })}><option value="">{labels.unassigned}</option>{extensions.map((extension) => <option key={extension} value={extension} disabled={occupied.has(extension)}>{extension}</option>)}</select></td>
    <td data-label={labels.status}><span className={`observation-pill ${observed ? "observed" : "unknown"}`} title={`${labels.lastObserved}: ${device.lastSeenAt ?? labels.unknown}`}><span />{observed ? labels.historyAvailable : labels.historyMissing}</span></td>
    <td className="row-actions" data-label=""><span className="save-message saved" role={message ? "status" : undefined}>{message}</span><button className="button primary save-button" disabled={!changed || saving} onClick={onSave}>{saving ? "…" : labels.save}</button></td>
  </tr>;
}

function AsteriskPage({ value, labels, language }: { value: AsteriskSnapshot | null; labels: Record<string, string>; language: Language }) {
  const endpointLabel = (state: string) => state === "online" ? labels.online : state === "offline" ? labels.offline : labels.statusUnavailable;
  return <>
    <div className="page-heading"><div><p className="eyebrow">ASTERISK</p><h1>{labels.asteriskTitle}</h1><p className="muted">{labels.asteriskSummary}</p></div></div>
    <section className="panel" aria-label={labels.asteriskTitle}>
      <div className="panel-heading"><div><h2>{labels.asterisk}</h2><p className="muted">{value?.ready ? (language === "en" ? "ARI is available" : "ARI доступен") : labels.statusUnavailable}</p></div><span className={`observation-pill ${value?.ready ? "online" : "unknown"}`}><span />{value?.ready ? (language === "en" ? "Ready" : "Готова") : labels.statusUnavailable}</span></div>
      <div className="metrics-grid"><section className="panel metric-card"><p className="muted">{labels.activeChannels}</p><strong>{value?.ready ? value.activeChannels : "—"}</strong></section></div>
      <div className="table-scroll"><table><thead><tr><th>{labels.phone}</th><th>{labels.status}</th></tr></thead><tbody>{(value?.endpoints ?? []).map((endpoint) => <tr key={endpoint.extension}><td>{endpoint.extension}</td><td><span className={`observation-pill ${endpoint.state}`}><span />{endpointLabel(endpoint.state)}</span></td></tr>)}</tbody></table></div>
    </section>
  </>;
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

function pageFromHash(hash: string): VoicePage {
  if (hash === "#/profiles") return "profiles";
  if (hash === "#/asterisk") return "asterisk";
  return "phones";
}
