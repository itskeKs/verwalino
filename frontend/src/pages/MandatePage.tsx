/**
 * Mandate list -- an authenticated view that fetches mandates
 * from GET /api/mandates.  Demonstrates tenant-scoped data access.
 */

import { useEffect, useState } from "react";
import { listMandates } from "../api/endpoints";
import type { Mandate } from "../api/types";

export function MandatePage() {
  const [mandates, setMandates] = useState<Mandate[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    listMandates()
      .then((data) => {
        if (!cancelled) setMandates(data);
      })
      .catch((err) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <h1>Mandate</h1>
      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p>Lade...</p>
      ) : mandates.length === 0 ? (
        <p className="empty-hint">Keine Mandate vorhanden.</p>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Typ</th>
                <th>Bezeichnung</th>
                <th>Beginn</th>
                <th>Ende</th>
              </tr>
            </thead>
            <tbody>
              {mandates.map((m) => (
                <tr key={m.id}>
                  <td>
                    <span className={`badge badge--${m.mandate_type.toLowerCase()}`}>
                      {m.mandate_type}
                    </span>
                  </td>
                  <td>{m.label || "-"}</td>
                  <td>{m.start_date}</td>
                  <td>{m.end_date ?? "offen"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
