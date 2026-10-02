import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

interface Policy { enabled: boolean; max_cost_usd: string; window_minutes: number; reservation_seconds: number }
interface Review { id: string; reviewed_at: string; reason: string; policy: Policy; policy_digest: string }
interface Status { policy: Policy; policy_digest: string; review: Review | null; matches_reviewed_policy: boolean }
const path = "/api/access-preparation/runtime-budget";

export function RuntimeBudgetPanel() {
  const [state, setState] = useState<Status | null>(null);
  const [reason, setReason] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Review | null>(null);
  const request = useRef<{ body: string; key: string } | null>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    let cancelled = false;
    api.get<Status>(path).then(value => {
      if (!cancelled) { setState(value); setVerified(true); }
    }).catch(() => {
      if (!cancelled) setError("Budgetregel nicht verfügbar. Bitte erneut laden.");
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  async function reload() {
    if (inFlight.current || loading) return;
    inFlight.current = true; setLoading(true); setError(null); setVerified(false); setAcknowledged(false);
    try { setState(await api.get<Status>(path)); setVerified(true); }
    catch { setError("Budgetregel nicht verfügbar. Bitte erneut laden."); }
    finally { inFlight.current = false; setLoading(false); }
  }

  async function review() {
    if (inFlight.current || loading || !verified || !state || !acknowledged || !reason.trim()) return;
    const body = { expected_review_id: state.review?.id ?? null, policy_digest: state.policy_digest, reason: reason.trim(), acknowledged: true };
    const serialized = JSON.stringify(body);
    if (request.current?.body !== serialized) request.current = { body: serialized, key: crypto.randomUUID() };
    inFlight.current = true; setBusy(true); setError(null);
    try {
      const saved = await api.post<Review>(`${path}/review`, body, { headers: { "Idempotency-Key": request.current.key } });
      setReceipt(saved); setVerified(false); setAcknowledged(false);
      try { setState(await api.get<Status>(path)); setVerified(true); }
      catch { setError("Prüfvermerk gespeichert. Der aktuelle Stand konnte nicht bestätigt werden. Bitte erneut laden."); }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setVerified(false); setAcknowledged(false);
        setError("Die Budgetregel oder ihr Prüfstand hat sich geändert. Bitte erneut laden und prüfen.");
      } else setError("Prüfvermerk nicht bestätigt. Erneut versuchen; derselbe Auftrag wird wiederverwendet.");
    } finally { inFlight.current = false; setBusy(false); }
  }

  return <section className="card min-w-0 space-y-4" aria-label="Budget für Wissensauskünfte">
    <h2>Budget für Wissensauskünfte</h2>
    <p>Prüfen Sie die im laufenden System konfigurierte Budgetregel. Die Bestätigung speichert diese Grenze für Wissensauskünfte und einen Prüfvermerk.</p>
    <button type="button" className="btn" disabled={busy || loading} onClick={() => void reload()}>Budgetregel erneut laden</button>
    {loading && <p role="status">Budgetregel wird geladen…</p>}
    {state && <>
      <dl className="grid gap-2 sm:grid-cols-2">
        <dt>Wissensauskünfte konfiguriert</dt><dd>{state.policy.enabled ? "Ein" : "Aus"}</dd>
        <dt>Budgetgrenze pro Organisation</dt><dd className="break-all">{state.policy.max_cost_usd} USD</dd>
        <dt>Budgetzeitraum</dt><dd>{state.policy.window_minutes} Minuten</dd>
        <dt>Reservierungsdauer</dt><dd>{state.policy.reservation_seconds} Sekunden</dd>
      </dl>
      {verified && <p>{state.matches_reviewed_policy ? "Die aktuelle Budgetregel ist geprüft." : "Die aktuelle Budgetregel benötigt eine ausdrückliche Prüfung."}</p>}
      {state.review && <p>Letzter Prüfvermerk vom {new Date(state.review.reviewed_at).toLocaleString("de-DE")} · {state.review.reason}</p>}
      {!state.matches_reviewed_policy && <>
        <label className="block space-y-1">Begründung der Budgetprüfung
          <textarea className="input w-full" value={reason} maxLength={2000} disabled={busy || loading} onChange={event => setReason(event.target.value)} />
        </label>
        <label className="flex items-start gap-2"><input type="checkbox" checked={acknowledged} disabled={busy || loading || !verified} onChange={event => setAcknowledged(event.target.checked)} />
          <span>Ich habe die angezeigte Budgetregel einschließlich Zeitraum und Reservierungsdauer geprüft.</span>
        </label>
        <button type="button" className="btn" disabled={busy || loading || !verified || !acknowledged || !reason.trim()} onClick={() => void review()}>Budgetregel bestätigen</button>
      </>}
    </>}
    {receipt && <p role="status" className="break-words">Prüfvermerk gespeichert · {new Date(receipt.reviewed_at).toLocaleString("de-DE")} · {receipt.reason}</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}
