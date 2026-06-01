/**
 * Top navigation bar -- always visible.
 * Shows app title, role indicator, and logout button.
 */

import { useAuth } from "../auth/AuthContext";
import "./Topbar.css";

interface Props {
  onToggleSidebar: () => void;
}

export function Topbar({ onToggleSidebar }: Props) {
  const { session, logout } = useAuth();

  return (
    <header className="topbar">
      <div className="topbar__left">
        <button
          className="topbar__hamburger"
          onClick={onToggleSidebar}
          aria-label="Navigation umschalten"
        >
          &#9776;
        </button>
        <span className="topbar__title">Hausverwaltung</span>
      </div>

      <div className="topbar__right">
        {session && (
          <>
            <span className="topbar__role" title="Aktuelle Rolle">
              {roleLabelDe(session.role)}
            </span>
            <button className="topbar__logout" onClick={logout}>
              Abmelden
            </button>
          </>
        )}
      </div>
    </header>
  );
}

function roleLabelDe(role: string): string {
  switch (role) {
    case "OWNER_ADMIN":
      return "Admin";
    case "CLERK":
      return "Sachbearbeiter";
    case "READ_ONLY":
      return "Nur-Lesen";
    default:
      return role;
  }
}
