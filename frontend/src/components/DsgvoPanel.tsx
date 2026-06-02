/**
 * DSGVO admin panel (M9) -- embedded on the person detail page, OWNER_ADMIN only.
 *
 * Shows the data-protection state of a person and what an erasure request would
 * do (RESTRICT if retention-bound, else ANONYMIZE), plus the retention bindings
 * and the governing retention date. Actions:
 *   - Löschung beantragen (Art. 17): restrict-or-anonymize via the engine decision.
 *   - Anonymisieren: explicit purge, refused (409) while still retention-bound.
 *   - Daten anzeigen (Art. 15/20 export).
 *
 * Mutations are confirmed (window.confirm) and surface backend 409 reasons.
 */

import { useEffect, useState, useCallback } from "react";
import {
  getDsgvoStatus,
  getDsgvoExport,
  requestPersonDeletion,
  anonymizePerson,
} from "../api/endpoints";
import type { DsgvoStatus, DsgvoExport, DsgvoBinding } from "../api/types";
import { ApiError } from "../api/client";

const BINDING_LABELS: Record<string, string> = {
  COST_ENTRY_VENDOR: "Kostenbeleg (als Dienstleister)",
  SETTLEMENT_ADDRESSEE: "Adressat einer finalisierten Abrechnung",
  FINALIZED_OWNERSHIP: "Eigentümer in finalisierter WEG-Abrechnung/Plan",
  DUNNING: "Adressat einer ausgestellten Mahnung",
};

interface Props {
  personId: string;
  onChanged?: () => void;
}

export function DsgvoPanel({ personId, onChanged }: Props) {
  const [status, setStatus] = useState<DsgvoStatus | null>(null);
  const [exportData, setExportData] = useState<DsgvoExport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    getDsgvoStatus(personId)
      .then(setStatus)
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [personId]);

  useEffect(() => {
    reload();
  }, [reload]);

  async function runAction(
    fn: () => Promise<DsgvoStatus>,
    confirmMsg: string,
  ) {
    if (!window.confirm(confirmMsg)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await fn();
      setStatus(result);
      setNotice(describeApplied(result.applied));
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function showExport() {
    setBusy(true);
    setError(null);
    try {
      setExportData(await getDsgvoExport(personId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return null;

  return (
    <div className="card" style={{ marginBottom: "1.5rem", borderLeft: "3px solid #6a4caf" }}>
      <h2>Datenschutz (DSGVO)</h2>

      {error && <p className="error-text">{error}</p>}
      {notice && <p style={{ color: "#1b7a37", fontSize: "0.85rem" }}>{notice}</p>}

      {status && (
        <>
          <table className="info-table">
            <tbody>
              <tr>
                <td>Status</td>
                <td>{describeState(status)}</td>
              </tr>
              <tr>
                <td>Beurteilung Löschanfrage</td>
                <td>
                  {status.already_anonymized
                    ? "Bereits anonymisiert"
                    : status.has_active_relationship
                      ? "Löschung blockiert — laufende vertragliche Beziehung"
                      : status.decision === "RESTRICT"
                        ? "Einschränkung (aufbewahrungspflichtig gebunden)"
                        : "Sofort anonymisierbar (keine Bindung)"}
                </td>
              </tr>
              {status.governing_retention_until && (
                <tr>
                  <td>Aufbewahrung bis</td>
                  <td style={{ fontFamily: "var(--mono)" }}>
                    {status.governing_retention_until}
                    {status.purgeable_now ? " (abgelaufen — anonymisierbar)" : ""}
                  </td>
                </tr>
              )}
              {status.deletion_requested_at && (
                <tr>
                  <td>Löschung beantragt am</td>
                  <td style={{ fontFamily: "var(--mono)" }}>
                    {status.deletion_requested_at.slice(0, 10)}
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          {status.bindings.length > 0 && (
            <div style={{ marginTop: "0.75rem" }}>
              <strong style={{ fontSize: "0.85rem" }}>Aufbewahrungsbindungen</strong>
              <ul style={{ fontSize: "0.8rem", color: "var(--text-muted)", margin: "0.4rem 0 0", paddingLeft: "1.2rem" }}>
                {status.bindings.map((b: DsgvoBinding, i) => (
                  <li key={i}>
                    {BINDING_LABELS[b.kind] ?? b.kind}
                    {b.retention_until ? ` — Aufbewahrung bis ${b.retention_until}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {!status.already_anonymized && (
            <div style={{ marginTop: "1rem", display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
              <button
                className="btn btn--small"
                disabled={busy}
                onClick={() =>
                  runAction(
                    () => requestPersonDeletion(personId),
                    status.decision === "RESTRICT" && !status.has_active_relationship
                      ? "Löschung beantragen? Die Person wird unter Verarbeitungseinschränkung gestellt (aufbewahrungspflichtig gebunden)."
                      : "Löschung beantragen? Ohne Aufbewahrungsbindung werden die Stammdaten sofort anonymisiert.",
                  )
                }
              >
                Löschung beantragen (Art. 17)
              </button>
              <button
                className="btn btn--small btn--danger"
                disabled={busy}
                onClick={() =>
                  runAction(
                    () => anonymizePerson(personId),
                    "Personenstammdaten unwiderruflich anonymisieren? Aufbewahrungspflichtige Belege bleiben unverändert.",
                  )
                }
              >
                Anonymisieren
              </button>
              <button className="btn btn--small" disabled={busy} onClick={showExport}>
                Daten anzeigen (Art. 15)
              </button>
            </div>
          )}

          <p style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.75rem" }}>
            Aufbewahrungspflichtige Belege (Buchungen, finalisierte Abrechnungen,
            Mahnungen) werden nie verändert; geändert werden ausschließlich die
            Personenstammdaten. Fristen/Aufbewahrungsdauer sind provisorisch
            ([§18] fachlich zu bestätigen, ADR-0012).
          </p>

          {exportData && (
            <details open style={{ marginTop: "0.75rem" }}>
              <summary style={{ cursor: "pointer", fontSize: "0.85rem" }}>
                Auskunft (Art. 15/20)
              </summary>
              <pre
                style={{
                  fontSize: "0.72rem",
                  background: "#f7f8fa",
                  padding: "0.75rem",
                  overflowX: "auto",
                  borderRadius: "4px",
                  marginTop: "0.4rem",
                }}
              >
                {JSON.stringify(exportData, null, 2)}
              </pre>
            </details>
          )}
        </>
      )}
    </div>
  );
}

function describeState(s: DsgvoStatus): string {
  if (s.already_anonymized) return "Anonymisiert";
  if (s.processing_restricted) return "Verarbeitung eingeschränkt (Art. 18)";
  return "Aktiv";
}

function describeApplied(applied: string): string {
  switch (applied) {
    case "RESTRICTED":
      return "Verarbeitungseinschränkung gesetzt (aufbewahrungspflichtig gebunden).";
    case "ANONYMIZED":
      return "Personenstammdaten anonymisiert.";
    case "NONE_ALREADY_ANONYMIZED":
      return "Person war bereits anonymisiert.";
    default:
      return "Aktion ausgeführt.";
  }
}
