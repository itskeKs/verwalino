import { useRef, useState } from "react";
import { api } from "../api/client";
import { usePagedList } from "../hooks/usePagedList";
import { MoreResults } from "./ListSearch";

interface Candidate {
  id: string; bank_label: string; value_date: string; direction: string; amount: string;
  payment_type: string; proof_status: "proven" | "review_required"; blocker: string | null; source_digest: string | null;
}
const blockers: Record<string, string> = {
  scope_units_not_supported: "Einheitenzuordnung muss einzeln geprüft werden",
  payment_not_booked: "Noch nicht gebucht",
  already_classified: "Bereits zugeordnet – Vorschau neu laden",
  unproven_settlement_source: "Zugehörige Abrechnung noch nicht eindeutig zugeordnet",
  conflicting_correction_account: "Stornokette passt nicht zum Konto",
  binding_semantics_conflict: "Zeitraum, Mandat oder Zahlungsart passen nicht zum Kreis",
  not_available: "Datensatz nicht verfügbar",
};

function PaymentReview({ scopeId }: { scopeId: string }) {
  const endpoint = `/api/accounting-scopes/${scopeId}/legacy-payments`;
  const page = usePagedList<Candidate>(endpoint);
  const [selected, setSelected] = useState<Candidate[]>([]);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<number | null>(null);
  const replay = useRef({ body: "", key: "" });
  function reload() { setSelected([]); setError(null); setResult(null); page.reload(); }
  async function apply(event: React.FormEvent) {
    event.preventDefault();
    if (!selected.length || selected.length > 200 || page.error || page.loading || busy) return;
    const body = { reason: reason.trim(), items: selected.map(item => ({ payment_id: item.id, source_digest: item.source_digest })).sort((a, b) => a.payment_id.localeCompare(b.payment_id)) };
    const serialized = JSON.stringify(body);
    if (replay.current.body !== serialized) replay.current = { body: serialized, key: crypto.randomUUID() };
    setBusy(true); setError(null); setResult(null);
    try {
      const response = await api.post<{ items: unknown[] }>(endpoint, body, { headers: { "Idempotency-Key": replay.current.key } });
      setSelected([]); setResult(response.items.length); page.reload();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <form onSubmit={apply}>
    <p>Die Vorschau prüft gebuchte Altzahlungen anhand ihres bereits zugeordneten Kontos. Eine Übernahme ergänzt die belegte Kreiszuordnung und erhält die ursprüngliche Buchung.</p>
    <fieldset disabled={busy} style={{ border: 0, padding: 0, minWidth: 0 }}>
      <button type="button" className="btn" onClick={reload}>Vorschau neu laden</button>
      {page.error && <p role="alert" className="error-text">Vorschau nicht verfügbar: {page.error}</p>}
      <div className="paged-table"><table className="data-table"><thead><tr><th>Auswahl</th><th>Konto / Datum</th><th>Betrag</th><th>Prüfung</th></tr></thead><tbody>
        {page.items.map(item => {
          const checked = selected.some(row => row.id === item.id);
          return <tr key={item.id}><td><input type="checkbox" aria-label={`Zahlung ${item.bank_label} ${item.value_date} auswählen`}
            checked={checked} disabled={item.proof_status !== "proven" || !item.source_digest || (!checked && selected.length >= 200)}
            onChange={e => { setResult(null); setSelected(e.target.checked ? [...selected, item] : selected.filter(row => row.id !== item.id)); }} /></td>
            <td>{item.bank_label}<br />{item.value_date}</td><td>{item.direction === "OUT" ? "−" : "+"}{item.amount} EUR</td>
            <td>{item.proof_status === "proven" ? "Durch Konto belegt" : blockers[item.blocker ?? ""] ?? "Einzelprüfung erforderlich"}</td></tr>;
        })}
      </tbody></table></div>
      {page.loading && <p role="status">Vorschau wird geprüft…</p>}
      {!page.loading && !page.error && !page.items.length && <p>Keine weiteren Altzahlungen auf den zugeordneten Konten gefunden.</p>}
      <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
      <p>{selected.length} Zahlungen ausgewählt (höchstens 200 je Übernahme).</p>
      <label>Begründung der Übernahme<input required maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label>
      <button type="submit" className="btn btn--primary" disabled={!selected.length || !reason.trim() || !!page.error || page.loading}>Auswahl verbindlich zuordnen</button>
    </fieldset>
    {error && <p role="alert" className="error-text">{error}</p>}
    {result !== null && <p role="status">{result} Zahlungen wurden zugeordnet. Die ursprünglichen Buchungen bleiben erhalten.</p>}
  </form>;
}

export function ScopePaymentReconciliation({ scopeId }: { scopeId: string }) {
  const [open, setOpen] = useState(false);
  return <section className="card"><h2>Altzahlungen zuordnen</h2>
    {open ? <PaymentReview key={scopeId} scopeId={scopeId} /> : <button className="btn" onClick={() => setOpen(true)}>Altzahlungen prüfen</button>}
  </section>;
}
