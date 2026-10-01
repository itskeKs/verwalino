import { useState } from "react";
import { api, ApiError } from "../api/client";

export function FinancialCoverageReview({ kind, subjectId, values }: {
  kind: "settlement" | "economic_plan"; subjectId: string; values: object;
}) {
  const [reason, setReason] = useState("");
  const [confirmedValues, setConfirmedValues] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savedValues, setSavedValues] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fingerprint = JSON.stringify({ kind, subjectId, values });
  const complete = confirmedValues === fingerprint;
  async function save() {
    setBusy(true); setError(null);
    try {
      await api.post("/api/financial-input-coverage", { subject_kind: kind, subject_id: subjectId,
        values, reason, confirmed_complete: true, source_document_ids: [] });
      setSavedValues(fingerprint);
    } catch (err) { setError(err instanceof ApiError ? err.message : String(err)); }
    finally { setBusy(false); }
  }
  return <section className="card" aria-label="Datengrundlage bestätigen">
    <h2>Datengrundlage bestätigen</h2>
    <p>Fehlende Werte sind unbekannt. Auch 0,00 und leere Listen benötigen eine vollständige Prüfung für den Abrechnungszeitraum.</p>
    <label>Grundlage der Prüfung<textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Zum Beispiel: Kontoauszüge Januar bis Dezember vollständig geprüft" /></label>
    <label><input type="checkbox" checked={complete} onChange={(e) => setConfirmedValues(e.target.checked ? fingerprint : null)} />Alle angezeigten Beträge und Listen sind vollständig geprüft, einschließlich bestätigter Nullbeträge.</label>
    <button className="btn" disabled={busy || !complete || !reason.trim()} onClick={save}>Datengrundlage speichern</button>
    {savedValues === fingerprint && <p role="status">Prüfung gespeichert. Geänderte Zahlungen oder Stammdaten erfordern eine neue Prüfung.</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </section>;
}
