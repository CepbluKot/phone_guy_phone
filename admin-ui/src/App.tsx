import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError, Profile, RouteSnapshot } from "./api";
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
    signOut: "Sign out",
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
    signOut: "Выйти",
  },
} satisfies Record<Language, Record<string, string>>;

export default function App() {
  const [language, setLanguage] = useState<Language>("en");
  const [csrfToken, setCsrfToken] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<RouteSnapshot | null>(null);
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

  const loadRoutes = useCallback(async () => {
    try {
      const routes = await api.routes();
      setSnapshot(routes);
      setDrafts(routes.extensions);
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
    void loadRoutes();
  }, [loadRoutes]);

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
      setStatus("login");
    }
  }

  async function save(extension: string) {
    if (!snapshot || !csrfToken) return;
    const profile = drafts[extension];
    setSaving(extension);
    setMessages((current) => ({ ...current, [extension]: undefined }));
    try {
      const next = await api.update(
        extension,
        profile,
        snapshot.revision,
        csrfToken,
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
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="/admin/" aria-label="Voice Control home">
          <span className="brand-mark">V</span>
          <span>
            Voice<span className="brand-light">Control</span>
          </span>
        </a>
        <p className="nav-label">
          {language === "en" ? "MANAGE" : "УПРАВЛЕНИЕ"}
        </p>
        <div className="nav-item active">
          <span className="phone-icon" aria-hidden="true">
            ▣
          </span>
          {t.phones}
        </div>
        <div className="sidebar-bottom">
          <span className="online-dot" />
          {t.service}
        </div>
      </aside>
      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb">
            {t.phones}
            <span>/</span>
            <strong>{t.title}</strong>
          </div>
          <div className="top-actions">
            <span className="service-badge">
              <span className="online-dot" />
              {t.service}
            </span>
            <LanguageControl language={language} onChange={setLanguage} />
            <button className="text-button" onClick={() => void signOut()}>
              {t.signOut}
            </button>
          </div>
        </header>
        <section className="page-content">
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
        </section>
      </main>
    </div>
  );
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
