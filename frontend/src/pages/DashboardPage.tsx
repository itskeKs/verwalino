/**
 * Dashboard -- an authenticated view that fetches and displays the
 * current organization from GET /api/organizations/me.
 *
 * This is a real API call to prove the end-to-end tenant flow.
 */

import { useEffect, useState } from "react";
import { getMyOrganization } from "../api/endpoints";
import type { Organization } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export function DashboardPage() {
  const { session } = useAuth();
  const [org, setOrg] = useState<Organization | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMyOrganization()
      .then((data) => {
        if (!cancelled) setOrg(data);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <h1>Dashboard</h1>
      {error && <p className="error-text">{error}</p>}
      {org ? (
        <div className="card">
          <h2>Organisation</h2>
          <table className="info-table">
            <tbody>
              <tr>
                <td>Name</td>
                <td>{org.name}</td>
              </tr>
              <tr>
                <td>ID</td>
                <td>
                  <code>{org.id}</code>
                </td>
              </tr>
              <tr>
                <td>Ihre Rolle</td>
                <td>{session?.role ?? "-"}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        !error && <p>Lade...</p>
      )}
    </div>
  );
}
