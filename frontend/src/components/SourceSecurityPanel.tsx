import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

type Mode = "PREPARATION" | "ENFORCED" | "PAUSED";
export interface ActivationDecision { id: string; snapshot_id: string; manifest_digest: string; assessment_digest: string }
interface Receipt { id: string; target_mode: Mode; changed_at: string; reason: string }
interface Status { mode: Mode; changed_at: string | null; last_transition: Receipt | null }
interface Evidence { current: boolean; reviewed: boolean; blocker_count: number; active_work_count: number; acceptance: ActivationDecision | null; manifest_digest: string; assessment_digest: string }
const names: Record<Mode, string> = { PREPARATION: "Vorbereitung", ENFORCED: "Zugriffsregeln aktiv", PAUSED: "Aktionen pausiert" };
const path = "/api/access-preparation/source-security";

export function SourceSecurityPanel({ decision }: { decision: ActivationDecision | null }) {
  const [selection, setSelection] = useState({ decision, generation: 0 });
  if (selection.decision !== decision) {
    setSelection({ decision, generation: selection.generation + 1 });
  }
  return <SourceSecurityControl key={selection.generation} decision={decision} />;
}

function SourceSecurityControl({ decision }: { decision: ActivationDecision | null }) {
  const [state, setState] = useState<Status | null>(null);
  const [eligible, setEligible] = useState(false);
  const [verified, setVerified] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [revision, setRevision] = useState(0);
  const request = useRef<{ serialized: string; key: string } | null>(null);
  const inFlight = useRef(false);
  useEffect(() => {
    let cancelled = false;
    async function read() {
      try {
        const current = await api.get<Status>(path);
        if (cancelled) return;
        setState(current); setVerified(true);
        // Pausing stays available even if snapshot or budget checks are unavailable.
        if (current.mode !== "ENFORCED" && decision) {
          try {
            const evidence = await api.get<Evidence>(`/api/access-preparation/snapshots/${decision.snapshot_id}/disposition`);
            if (!cancelled) {
              const valid = evidence.current && evidence.reviewed && evidence.blocker_count === 0 && evidence.active_work_count === 0
                && evidence.acceptance?.id === decision.id && evidence.manifest_digest === decision.manifest_digest && evidence.assessment_digest === decision.assessment_digest;
              setEligible(valid);
              if (!valid) setError("Die ausgewählte Grundlage ist nicht mehr aktuell. Bitte einen neuen Prüfstand erfassen, prüfen und annehmen.");
            }
          } catch { if (!cancelled) setError("Aktivierungsgrundlage nicht verfügbar. Bitte erneut laden."); }
        }
      } catch { if (!cancelled) setError("Aktueller Zugriffsmodus nicht verfügbar. Bitte erneut laden."); }
      finally { if (!cancelled) setLoading(false); }
    }
    void read();
    return () => { cancelled = true; };
  }, [decision, revision]);
  function reload() {
    if (inFlight.current || loading) return;
    setVerified(false); setEligible(false); setAck(false); setLoading(true); setError(null); setRevision(value => value + 1);
  }
  const pause = state?.mode === "ENFORCED";
  const allowed = verified && (pause || eligible);
  const action = pause ? "Aktionen pausieren" : state?.mode === "PAUSED" ? "Aktionen wieder aufnehmen" : "Zugriffsregeln aktivieren";
  async function submit() {
    if (inFlight.current || loading || !allowed || !state || !ack || !reason.trim()) return;
    const body = { target_mode: pause ? "PAUSED" : "ENFORCED", expected_mode: state.mode, expected_changed_at: state.changed_at,
      expected_transition_id: state.last_transition?.id ?? null, acceptance_id: pause ? null : decision?.id,
      manifest_digest: pause ? null : decision?.manifest_digest, assessment_digest: pause ? null : decision?.assessment_digest,
      reason: reason.trim(), acknowledged: true };
    const serialized = JSON.stringify(body);
    if (request.current?.serialized !== serialized) request.current = { serialized, key: crypto.randomUUID() };
    inFlight.current = true; setBusy(true); setError(null);
    try {
      setReceipt(await api.post<Receipt>(`${path}/transitions`, body, { headers: { "Idempotency-Key": request.current.key } }));
      setVerified(false); setEligible(false); setAck(false);
      try { setState(await api.get<Status>(path)); setVerified(true); }
      catch { setError("Entscheidung gespeichert. Der aktuelle Zugriffsmodus ist nicht bestätigt. Bitte erneut laden."); }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setVerified(false); setEligible(false); setAck(false);
        setError("Zustand oder Prüfgrundlage hat sich geändert. Bitte erneut laden und die Entscheidung neu prüfen.");
      } else setError("Umstellung nicht bestätigt. Erneut versuchen; derselbe Auftrag wird wiederverwendet.");
    } finally { inFlight.current = false; setBusy(false); }
  }
  return <section id="zugriffsmodus" className="card min-w-0 space-y-4" aria-label="Zugriffsmodus steuern">
    <h2>Zugriffsmodus</h2>
    {loading && <p role="status">Zugriffsmodus wird geladen…</p>}
    {state && <p>{verified ? "Aktueller Modus" : "Zuletzt gelesener Modus"}: <strong>{names[state.mode]}</strong></p>}
    <p>Die Aktivierung setzt die geprüften Daten- und Quellenrechte durch. Eine Rückkehr zum bisherigen Zugriff ist danach ausgeschlossen. Pausieren stoppt Aktionen und behält die eingeschränkten Leserechte bei.</p>
    <button className="btn" type="button" disabled={busy || loading} onClick={reload}>Zugriffsmodus erneut laden</button>
    {!pause && <>{decision ? <p className="break-all">Ausgewählter Prüfstand: {decision.snapshot_id}</p>
      : <p>Zum Aktivieren oder Wiederaufnehmen unten einen aktuellen Prüfstand prüfen, die Altbestandsentscheidung speichern und als Grundlage auswählen.</p>}
      {state?.mode === "PAUSED" && <p>Die Wiederaufnahme benötigt einen neuen Prüfstand nach der letzten Pause.</p>}</>}
    <label className="block space-y-1">Begründung der Umstellung<textarea className="input w-full" maxLength={2000} value={reason} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
    <label className="flex items-start gap-2"><input type="checkbox" checked={ack} disabled={busy || loading || !allowed} onChange={event => setAck(event.target.checked)} /><span>{pause
      ? "Ich bestätige die Pause der Aktionen bei weiterhin eingeschränkten Leserechten."
      : "Ich bestätige die Umstellung anhand des ausgewählten Prüfstands und die dauerhafte Durchsetzung der geprüften Zugriffsregeln."}</span></label>
    <button className="btn" type="button" disabled={busy || loading || !allowed || !ack || !reason.trim()} onClick={() => void submit()}>{action}</button>
    {receipt && <p role="status">Entscheidung gespeichert: {names[receipt.target_mode]} · {new Date(receipt.changed_at).toLocaleString("de-DE")} · {receipt.reason}. Der aktuelle Modus wird gesondert angezeigt.</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}
