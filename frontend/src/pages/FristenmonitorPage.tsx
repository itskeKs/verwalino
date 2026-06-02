/**
 * Fristenmonitor (M9) -- read-only overview of statutory and provisional
 * deadlines: §556 III BK deadline (hard), WEG/Wirtschaftsplan deadlines
 * (provisional [§18]), overdue open items + Beleg retention (informational).
 *
 * Sorted by urgency by the backend (OVERDUE -> DUE -> UPCOMING -> INFO -> OK).
 */

import { useEffect, useState, useCallback } from "react";
import { listFristen } from "../api/endpoints";
import type { DeadlineItem, DeadlineStatus } from "../api/types";
import { ApiError } from "../api/client";

const KIND_LABELS: Record<string, string> = {
  BK_ABRECHNUNG: "Betriebskostenabrechnung",
  WEG_JAHRESABRECHNUNG: "WEG-Jahresabrechnung",
  WEG_VERMOEGENSBERICHT: "Vermögensbericht",
  WIRTSCHAFTSPLAN: "Wirtschaftsplan",
  OFFENE_POSTEN: "Überfällige offene Posten",
  BELEG_AUFBEWAHRUNG: "Beleg-Aufbewahrung",
};

const STATUS_LABELS: Record<DeadlineStatus, string> = {
  OVERDUE: "Überfällig",
  DUE: "Fällig",
  UPCOMING: "Anstehend",
  INFO: "Info",
  OK: "Erledigt",
};

const STATUS_STYLE: Record<DeadlineStatus, React.CSSProperties> = {
  OVERDUE: { background: "#fdecea", color: "#b71c1c", border: "1px solid #f5c6cb" },
  DUE: { background: "#fff4e5", color: "#a15c00", border: "1px solid #ffe0b2" },
  UPCOMING: { background: "#e8f0fe", color: "#1a4fb4", border: "1px solid #c3d7fb" },
  INFO: { background: "#eef1f4", color: "#555", border: "1px solid #dde2e7" },
  OK: { background: "#e7f4ea", color: "#1b7a37", border: "1px solid #c6e6cf" },
};

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function FristenmonitorPage() {
  const [items, setItems] = useState<DeadlineItem[]>([]);
  const [referenceDate, setReferenceDate] = useState<string>(todayIso());
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    listFristen(referenceDate)
      .then(setItems)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          setError("Nicht authentifiziert. Bitte erneut anmelden.");
        } else {
          setError(String(err));
        }
      })
      .finally(() => setLoading(false));
  }, [referenceDate]);

  useEffect(() => {
    reload();
  }, [reload]);

  const actionCount = items.filter(
    (i) => i.status === "OVERDUE" || i.status === "DUE",
  ).length;

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "1rem",
          flexWrap: "wrap",
          gap: "0.5rem",
        }}
      >
        <h1 style={{ margin: 0 }}>Fristenmonitor</h1>
        <label style={{ fontSize: "0.85rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
          Stichtag
          <input
            type="date"
            value={referenceDate}
            onChange={(e) => setReferenceDate(e.target.value || todayIso())}
            style={{ padding: "0.35rem 0.5rem" }}
          />
        </label>
      </div>

      <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginBottom: "1rem" }}>
        {actionCount > 0
          ? `${actionCount} Frist(en) erfordern Aufmerksamkeit (überfällig oder fällig).`
          : "Keine überfälligen oder fälligen Fristen zum Stichtag."}{" "}
        Die BK-Frist (§556 III BGB) ist gesetzlich; WEG-/Wirtschaftsplan-Fristen
        sind provisorisch ([§18] fachlich zu bestätigen).
      </p>

      {error && <p className="error-text">{error}</p>}

      {loading ? (
        <p>Lade...</p>
      ) : items.length === 0 ? (
        <p className="empty-hint">Keine Fristen ermittelt.</p>
      ) : (
        <div className="card">
          <div style={{ overflowX: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Art</th>
                  <th>Bezug</th>
                  <th>Fällig am</th>
                  <th>Hinweis</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, idx) => (
                  <tr key={`${it.kind}-${it.reference}-${idx}`}>
                    <td>
                      <span
                        className="badge"
                        style={{ ...STATUS_STYLE[it.status], whiteSpace: "nowrap" }}
                      >
                        {STATUS_LABELS[it.status] ?? it.status}
                      </span>
                    </td>
                    <td>
                      {KIND_LABELS[it.kind] ?? it.kind}
                      <div style={{ marginTop: "0.2rem", display: "flex", gap: "0.3rem", flexWrap: "wrap" }}>
                        {it.is_statutory && (
                          <span
                            className="badge"
                            style={{ background: "#ede7f6", color: "#4527a0", fontSize: "0.7rem" }}
                          >
                            gesetzlich
                          </span>
                        )}
                        {it.is_provisional && (
                          <span
                            className="badge"
                            style={{ background: "#eef1f4", color: "#666", fontSize: "0.7rem" }}
                          >
                            provisorisch [§18]
                          </span>
                        )}
                      </div>
                    </td>
                    <td>{it.reference}</td>
                    <td style={{ fontFamily: "var(--mono)", whiteSpace: "nowrap" }}>
                      {it.due_date ?? "—"}
                    </td>
                    <td style={{ fontSize: "0.8rem", color: "var(--text-muted)", maxWidth: "26rem" }}>
                      {it.detail}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
