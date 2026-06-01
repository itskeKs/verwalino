/**
 * Collapsible sidebar navigation.
 *
 * On desktop (>768px): always visible in a fixed-width column.
 * On mobile: slides in as an overlay when `open` is true.
 *
 * Admin-only links are shown only when the session role is OWNER_ADMIN,
 * demonstrating role-awareness in the UI (architecture.md section 4.2).
 */

import { NavLink } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import "./Sidebar.css";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function Sidebar({ open, onClose }: Props) {
  const { session } = useAuth();
  const isAdmin = session?.role === "OWNER_ADMIN";

  return (
    <nav className={`sidebar ${open ? "sidebar--open" : ""}`}>
      <ul className="sidebar__nav">
        <li>
          <NavLink to="/" className={navLinkClass} onClick={onClose}>
            Dashboard
          </NavLink>
        </li>
        <li>
          <NavLink to="/mandate" className={navLinkClass} onClick={onClose}>
            Mandate
          </NavLink>
        </li>

        {/* M1 Stammdaten */}
        <li className="sidebar__divider" />
        <li className="sidebar__section-label">Stammdaten</li>
        <li>
          <NavLink to="/objekte" className={navLinkClass} onClick={onClose}>
            Objekte
          </NavLink>
        </li>
        <li>
          <NavLink to="/personen" className={navLinkClass} onClick={onClose}>
            Personen
          </NavLink>
        </li>

        {/* M2 Kosten & Belege */}
        <li className="sidebar__divider" />
        <li className="sidebar__section-label">Kosten &amp; Belege</li>
        <li>
          <NavLink to="/kosten" className={navLinkClass} onClick={onClose}>
            Kostenpositionen
          </NavLink>
        </li>
        <li>
          <NavLink to="/belege" className={navLinkClass} onClick={onClose}>
            Belege
          </NavLink>
        </li>

        {/* Admin-only section */}
        {isAdmin && (
          <>
            <li className="sidebar__divider" />
            <li className="sidebar__section-label">Verwaltung</li>
            <li>
              <NavLink
                to="/users"
                className={navLinkClass}
                onClick={onClose}
              >
                Benutzer
              </NavLink>
            </li>
          </>
        )}
      </ul>
    </nav>
  );
}

function navLinkClass({
  isActive,
}: {
  isActive: boolean;
}): string {
  return `sidebar__link ${isActive ? "sidebar__link--active" : ""}`;
}
