import type { ReactNode } from "react";
import {
  Activity,
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Headphones,
  LayoutGrid,
  Smartphone,
  UserRound,
  type LucideIcon,
} from "lucide-react";

export type VoicePage = "phones" | "existing" | "unassigned" | "profiles" | "load";

type VoiceShellLabels = {
  appName: string;
  manage: string;
  phones: string;
  existingPhones: string;
  newUnassigned: string;
  voiceProfiles: string;
  load: string;
  browserPhone: string;
  service: string;
  signOut: string;
  collapseSidebar: string;
  expandSidebar: string;
};

const navigation: Array<{ page: VoicePage; label: keyof Pick<VoiceShellLabels, "phones" | "existingPhones" | "newUnassigned" | "voiceProfiles" | "load">; icon: LucideIcon }> = [
  { page: "phones", label: "phones", icon: LayoutGrid },
  { page: "existing", label: "existingPhones", icon: Smartphone },
  { page: "unassigned", label: "newUnassigned", icon: UserRound },
  { page: "profiles", label: "voiceProfiles", icon: AudioLines },
  { page: "load", label: "load", icon: Activity },
];

export function VoiceShell({
  page,
  pageTitle,
  sectionTitle,
  labels,
  sidebarCollapsed,
  onSidebarCollapsedChange,
  onNavigate,
  languageControl,
  onSignOut,
  children,
}: {
  page: VoicePage;
  pageTitle: string;
  sectionTitle: string;
  labels: VoiceShellLabels;
  sidebarCollapsed: boolean;
  onSidebarCollapsedChange: (collapsed: boolean) => void;
  onNavigate: (page: VoicePage) => void;
  languageControl: ReactNode;
  onSignOut?: () => void;
  children: ReactNode;
}) {
  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : "sidebar-expanded"}`}>
      <aside className="sidebar">
        <div className="sidebar-header">
          {!sidebarCollapsed ? (
            <a className="brand" href="/admin/" aria-label={`${labels.appName} home`}>
              <span>{labels.appName.split(" ")[0]}<span className="brand-light"> {labels.appName.split(" ").slice(1).join(" ")}</span></span>
            </a>
          ) : null}
          <button
            className="sidebar-toggle"
            type="button"
            aria-label={sidebarCollapsed ? labels.expandSidebar : labels.collapseSidebar}
            aria-expanded={!sidebarCollapsed}
            title={sidebarCollapsed ? labels.expandSidebar : labels.collapseSidebar}
            onClick={() => onSidebarCollapsedChange(!sidebarCollapsed)}
          >
            {sidebarCollapsed ? <ChevronRight aria-hidden="true" /> : <ChevronLeft aria-hidden="true" />}
          </button>
        </div>

        <nav className="voice-navigation" aria-label={labels.manage}>
          {!sidebarCollapsed ? <p className="nav-label">{labels.manage.toUpperCase()}</p> : null}
          {navigation.map(({ page: targetPage, label, icon: Icon }) => {
            const active = page === targetPage;
            return (
              <button
                key={targetPage}
                className={`nav-item ${active ? "active" : ""}`}
                type="button"
                aria-label={labels[label]}
                aria-current={active ? "page" : undefined}
                title={sidebarCollapsed ? labels[label] : undefined}
                onClick={() => onNavigate(targetPage)}
              >
                <Icon className="nav-icon" aria-hidden="true" />
                <span className="nav-item-label">{labels[label]}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-divider" />
        <a className="nav-item browser-phone-link" href="/phone/" title={sidebarCollapsed ? labels.browserPhone : undefined}>
          <Headphones className="nav-icon" aria-hidden="true" />
          <span className="nav-item-label">{labels.browserPhone}</span>
        </a>

        <div className="sidebar-bottom">
          <span className="online-dot" />
          <span className="sidebar-bottom-label">{labels.service}</span>
        </div>
      </aside>

      <main className="main-content">
        <header className="topbar">
          <div className="breadcrumb" aria-label="Breadcrumb">
            <span>{sectionTitle}</span>
            <span aria-hidden="true">/</span>
            <strong>{pageTitle}</strong>
          </div>
          <div className="top-actions">
            <span className="service-badge"><span className="online-dot" />{labels.service}</span>
            {languageControl}
            {onSignOut ? <button className="text-button" type="button" onClick={onSignOut}>{labels.signOut}</button> : null}
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
