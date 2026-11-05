/**
 * Shared routed work with personal Klar/Warm navigation and design tokens.
 */

import { useState, type ReactNode } from "react";
import { OrganizationDeletionBanner } from "./OrganizationDeletionBanner";
import { Topbar } from "./Topbar";
import { Sidebar } from "./Sidebar";
import { useDesignTheme } from "../design/useDesignTheme";
import { NavLink } from "react-router-dom";
import { LayoutDashboard, Building2, Inbox } from "lucide-react";

interface Props {
  children: ReactNode;
}

export function AppShell({ children }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const { theme } = useDesignTheme();

  return (
    <div className={`app-layout app-layout--${theme}`} data-design-theme={theme}>
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <div className="app-workspace">
        <Topbar onToggleSidebar={() => setSidebarOpen((o) => !o)} />
        <nav className="mobile-shortcuts" aria-label="Schnellnavigation">
          <NavLink to="/" end><LayoutDashboard aria-hidden="true" />Übersicht</NavLink>
          <NavLink to="/objekte"><Building2 aria-hidden="true" />Objekte</NavLink>
          <NavLink to="/posteingang"><Inbox aria-hidden="true" />Posteingang</NavLink>
        </nav>
        <main className="app-main">
          <div className="app-page"><OrganizationDeletionBanner />{children}</div>
        </main>
      </div>
    </div>
  );
}
