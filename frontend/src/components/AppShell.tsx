/**
 * Responsive application shell: Topbar + collapsible Sidebar + Content area.
 *
 * Desktop-first (sidebar always visible), on narrow screens the sidebar
 * collapses and can be toggled via a hamburger button in the topbar.
 */

import { useState, type ReactNode } from "react";
import { Topbar } from "./Topbar";
import { Sidebar } from "./Sidebar";
import "./AppShell.css";

interface Props {
  children: ReactNode;
}

export function AppShell({ children }: Props) {
  const [sidebarOpen, setSidebarOpen] = useState(false);

  return (
    <div className="app-shell">
      <Topbar onToggleSidebar={() => setSidebarOpen((o) => !o)} />
      <div className="app-shell__body">
        <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        {/* Overlay to close sidebar on mobile when tapping outside */}
        {sidebarOpen && (
          <div
            className="sidebar-overlay"
            onClick={() => setSidebarOpen(false)}
          />
        )}
        <main className="app-shell__content">{children}</main>
      </div>
    </div>
  );
}
