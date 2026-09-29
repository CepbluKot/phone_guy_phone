import { useState, type ReactNode } from "react";
import {
  AudioLines,
  ChevronLeft,
  ChevronRight,
  Headphones,
  LayoutGrid,
  Menu,
  X,
  type LucideIcon,
} from "lucide-react";

export type VoicePage = "phones" | "profiles";

type VoiceShellLabels = {
  appName: string;
  manage: string;
  phones: string;
  voiceProfiles: string;
  adminHome: string;
  browserPhone: string;
  signOut: string;
  collapseSidebar: string;
  expandSidebar: string;
  openMobileNavigation: string;
  closeMobileNavigation: string;
  dismissMobileNavigation: string;
};

const navigation: Array<{ page: VoicePage; label: keyof Pick<VoiceShellLabels, "phones" | "voiceProfiles">; icon: LucideIcon }> = [
  { page: "phones", label: "phones", icon: LayoutGrid },
  { page: "profiles", label: "voiceProfiles", icon: AudioLines },
];

export function VoiceShell({
  page,
  labels,
  sidebarCollapsed,
  onSidebarCollapsedChange,
  onNavigate,
  onSignOut,
  children,
}: {
  page: VoicePage;
  labels: VoiceShellLabels;
  sidebarCollapsed: boolean;
  onSidebarCollapsedChange: (collapsed: boolean) => void;
  onNavigate: (page: VoicePage) => void;
  onSignOut?: () => void;
  children: ReactNode;
}) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : "sidebar-expanded"} ${mobileNavOpen ? "mobile-nav-open" : ""}`}>
      <aside id="voice-sidebar" className="sidebar">
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
                onClick={() => { onNavigate(targetPage); setMobileNavOpen(false); }}
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
          <span className="online-dot" aria-hidden="true" />
        </div>
      </aside>
      {mobileNavOpen && <button className="mobile-nav-backdrop" type="button" aria-label={labels.dismissMobileNavigation} onClick={() => setMobileNavOpen(false)} />}

      <main className="main-content">
        <header className="topbar">
          <button className="mobile-menu-toggle" type="button" aria-label={mobileNavOpen ? labels.closeMobileNavigation : labels.openMobileNavigation} aria-expanded={mobileNavOpen} aria-controls="voice-sidebar" onClick={() => setMobileNavOpen((open) => !open)}>
            {mobileNavOpen ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
          </button>
          <nav className="breadcrumb" aria-label="Breadcrumb">
            <a href="#/phones" onClick={(event) => { event.preventDefault(); onNavigate("phones"); }}>{labels.adminHome}</a>
            <span aria-hidden="true">/</span>
            <strong>{page === "phones" ? labels.phones : labels.voiceProfiles}</strong>
          </nav>
          <div className="top-actions">
            {onSignOut ? <button className="text-button" type="button" onClick={onSignOut}>{labels.signOut}</button> : null}
          </div>
        </header>
        {children}
      </main>
    </div>
  );
}
