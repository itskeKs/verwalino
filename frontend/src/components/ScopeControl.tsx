import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

interface ControlEvent { id: string; mode: "compatibility" | "paused"; reason: string; created_at: string }
interface ControlState { mode: "compatibility" | "paused"; event: ControlEvent | null }
const endpoint = "/api/accounting-scopes/control";

export function ScopeControl() {
  const [state, setState] = useState<ControlState | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const replay = useRef({ body: "", key: "" });
  useEffect(() => {
    const controller = new AbortController();
    api.get<ControlState>(endpoint, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setState(value); })
      .catch(e => { if (!controller.signal.aborted) setError(`Status nicht verfügbar: ${String(e)}`); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision]);
  function reload() {
    setState(null); setError(null); setLoading(true); setRevision(value => value + 1);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!state || loading || busy || !reason.trim()) return;
    const body = { mode: state.mode === "paused" ? "compatibility" : "paused", expected_event_id: state.event?.id ?? null, reason: reason.trim() };
    const serialized = JSON.stringify(body);
    if (replay.current.body !== serialized) replay.current = { body: serialized, key: crypto.randomUUID() };
    setBusy(true); setError(null);
    try {
      await api.post<ControlEvent>(endpoint, body, { headers: { "Idempotency-Key": replay.current.key } });
      // A replay receipt can precede a later resume. Only a fresh GET is current.
      setReason(""); reload();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <section className="card" aria-label="Bereichsbezogene Finanzaktionen">
    <h2>Bereichsbezogene Finanzaktionen</h2>
    <p>Die Pause stoppt Änderungen und Rechnungsjobs für bereits zugeordnete Finanzbereiche in dieser Organisation. Lesen und Exportieren bleiben mit den vorhandenen Rechten möglich.</p>
    <p>Unzugeordnete Altbestände laufen im bisherigen Betrieb weiter. Dies ist keine vollständige Buchungssperre. Die vollständige Zugriffsbeschränkung für den Teambetrieb ist weiterhin nicht aktiviert.</p>
    {loading && <p role="status">Status wird geladen…</p>}
    {state && <>
      <p role="status"><strong>{state.mode === "paused" ? "Bereichsbezogene Finanzaktionen pausiert" : "Bereichsbezogene Finanzaktionen nach bestehenden Rechten aktiv"}</strong></p>
      {state.event && <p>Letzte Änderung: {new Date(state.event.created_at).toLocaleString("de-DE")} · {state.event.reason}</p>}
      <p>Eine Wiederaufnahme verwendet die aktuell gültigen Rechte. Widerrufene Freigaben werden nicht wiederhergestellt.</p>
      <form onSubmit={submit}>
        <fieldset disabled={busy || loading} style={{ border: 0, padding: 0, minWidth: 0 }}>
          <label>Begründung der Betriebsänderung<input required maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label>
          <button className="btn" type="submit" disabled={!reason.trim()}>{state.mode === "paused" ? "Bereichsbezogene Finanzaktionen wieder aufnehmen" : "Bereichsbezogene Finanzaktionen pausieren"}</button>
        </fieldset>
      </form>
    </>}
    {error && <p role="alert" className="error-text">{error}</p>}
    <button className="btn" type="button" disabled={busy || loading} onClick={reload}>Betriebsstatus neu laden</button>
  </section>;
}
