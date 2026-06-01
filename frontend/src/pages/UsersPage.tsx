/**
 * Users page -- visible only for OWNER_ADMIN.
 * Demonstrates role-based visibility: the sidebar link is hidden for
 * non-admin roles, and the page itself guards access.
 */

import { useAuth } from "../auth/AuthContext";

export function UsersPage() {
  const { session } = useAuth();

  if (session?.role !== "OWNER_ADMIN") {
    return (
      <div>
        <h1>Benutzer</h1>
        <p className="error-text">
          Zugriff nur fuer Inhaber/Admin.
        </p>
      </div>
    );
  }

  return (
    <div>
      <h1>Benutzer</h1>
      <div className="card">
        <p>
          Benutzerverwaltung -- Platzhalter fuer M1+. Hier werden
          Benutzer und deren Rollen innerhalb dieser Organisation
          verwaltet.
        </p>
      </div>
    </div>
  );
}
