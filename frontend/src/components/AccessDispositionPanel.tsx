import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import { usePagedList } from "../hooks/usePagedList";
import { MoreResults } from "./ListSearch";
import { coverageFindings, coverageKinds } from "./accessCoverageLabels";

import type { ActivationDecision } from "./SourceSecurityPanel";

interface Receipt extends ActivationDecision { id: string; accepted_at: string; reason: string; retained_count: number; team_count: number }
interface Status {
  manifest_digest: string; assessment_digest: string; current: boolean; reviewed: boolean; acceptance_available: boolean;
  ready_count: number; retained_count: number; team_count: number; blocker_count: number; active_work_count: number; acceptance: Receipt | null;
}
interface Item { id: string; kind: string; resource_id: string; findings: string[]; disposition: "READY" | "RETAIN_BLOCKED" | "TEAM_REVIEW" | "BLOCKED" }
const labels = { READY: "Keine zusätzliche Entscheidung erforderlich", RETAIN_BLOCKED: "Nach Umstellung weiterhin gesperrt aufbewahren", TEAM_REVIEW: "Teamzuordnung ausdrücklich prüfen", BLOCKED: "Vor einer Entscheidung zu klären" };

export function AccessDispositionPanel({ snapshotId, onDecisionSelected }: { snapshotId: string; onDecisionSelected?: (decision: ActivationDecision) => void }) {
  const path = `/api/access-preparation/snapshots/${snapshotId}/disposition`;
  const page = usePagedList<Item>(`${path}/items`);
  const [state, setState] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [team, setTeam] = useState(false);
  const [retention, setRetention] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const request = useRef<{ body: string; key: string } | null>(null);
  const inFlight = useRef(false);
  useEffect(() => {
    let cancelled = false;
    api.get<Status>(path).then(value => { if (!cancelled) { setState(value); setVerified(true); } })
      .catch(() => { if (!cancelled) setError("Entscheidungsgrundlage nicht verfügbar. Bitte erneut laden."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [path]);
  async function reload() {
    if (inFlight.current || loading) return;
    inFlight.current = true; setLoading(true); setVerified(false); setError(null); setTeam(false); setRetention(false);
    try { setState(await api.get<Status>(path)); setVerified(true); }
    catch { setError("Entscheidungsgrundlage nicht verfügbar. Bitte erneut laden."); }
    finally { inFlight.current = false; setLoading(false); }
  }
  const allLoaded = state && !page.loading && !page.error && !page.nextCursor
    && page.items.length === state.ready_count + state.retained_count + state.team_count + state.blocker_count;
  async function accept() {
    if (inFlight.current || loading || !verified || !state?.acceptance_available || !allLoaded || !team || !retention || !reason.trim()) return;
    const body = { manifest_digest: state.manifest_digest, assessment_digest: state.assessment_digest, reason: reason.trim(), team_acknowledged: true, retention_acknowledged: true };
    const serialized = JSON.stringify(body);
    if (request.current?.body !== serialized) request.current = { body: serialized, key: crypto.randomUUID() };
    inFlight.current = true; setBusy(true); setError(null);
    try {
      setReceipt(await api.post<Receipt>(`${path}/accept`, body, { headers: { "Idempotency-Key": request.current.key } }));
      setVerified(false); setTeam(false); setRetention(false);
      try { setState(await api.get<Status>(path)); setVerified(true); }
      catch { setError("Entscheidung gespeichert. Der aktuelle Stand ist nicht bestätigt. Bitte erneut laden."); }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setVerified(false); setTeam(false); setRetention(false);
        setError("Die Grundlage ist veraltet oder enthält offene Punkte. Bitte erneut laden und bei Bedarf einen neuen Prüfstand erfassen.");
      } else setError("Entscheidung nicht bestätigt. Erneut versuchen; derselbe Auftrag wird wiederverwendet.");
    } finally { inFlight.current = false; setBusy(false); }
  }
  const saved = state?.acceptance ?? receipt;
  return <section className="card min-w-0 space-y-4" aria-label="Entscheidung über Altbestände">
    <h3>Entscheidung über Altbestände und Teamzuordnung</h3>
    <p>Diese Entscheidung dokumentiert ausdrücklich beibehaltene Sperren nach einer späteren Umstellung. Sie erteilt keine Freigaben und aktiviert keine Zugriffsregeln. Bereits vorgesehene administrative Quarantäneprüfungen bleiben möglich.</p>
    <button className="btn" type="button" disabled={busy || loading} onClick={() => void reload()}>Entscheidungsgrundlage erneut laden</button>
    {loading && <p role="status">Entscheidungsgrundlage wird geladen…</p>}
    {state && <>
      <p>{state.retained_count} gesperrte Altbestände · {state.team_count} Teamzuordnungen · {state.blocker_count} offene Einträge · {state.active_work_count} laufende Vorgänge</p>
      {verified && !state.current && <p role="alert">Dieser Prüfstand ist veraltet. Bitte einen neuen Stand erfassen und prüfen.</p>}
      {!state.reviewed && <p>Zuerst den vollständigen Prüfstand als geprüft vermerken.</p>}
      {(state.blocker_count > 0 || state.active_work_count > 0) && <p>Offene Einträge und laufende Vorgänge müssen vor der Entscheidung geklärt werden.</p>}
    </>}
    {page.error && <div><p role="alert">Entscheidungseinträge nicht verfügbar.</p><button className="btn" type="button" disabled={busy || page.loading} onClick={page.reload}>Entscheidungseinträge erneut laden</button></div>}
    <div className="space-y-3">{page.items.map(item => <article key={item.id} className="rounded border border-slate-200 p-3 space-y-1">
      <h4>{coverageKinds[item.kind] ?? "Quellenbezug"} · {labels[item.disposition]}</h4>
      <p className="break-all text-sm">Kennung: {item.resource_id}</p>
      {item.findings.length > 0 && <ul className="list-disc pl-5">{item.findings.map(code => <li key={code}>{coverageFindings[code] ?? "Dieser Eintrag benötigt eine gesonderte Prüfung."}</li>)}</ul>}
    </article>)}</div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
    {saved ? <p role="status">Entscheidung gespeichert · {new Date(saved.accepted_at).toLocaleString("de-DE")} · {saved.reason}. Diese Entscheidung erteilt keine Freigaben.</p> : <>
      <label className="block space-y-1">Begründung der Altbestandsentscheidung<textarea className="input w-full" value={reason} maxLength={2000} disabled={busy} onChange={event => setReason(event.target.value)} /></label>
      <label className="flex items-start gap-2"><input type="checkbox" checked={team} disabled={busy || loading || !verified} onChange={event => setTeam(event.target.checked)} /><span>Ich habe die Teamzuordnungen und ausdrücklich erteilten Freigaben geprüft.</span></label>
      <label className="flex items-start gap-2"><input type="checkbox" checked={retention} disabled={busy || loading || !verified} onChange={event => setRetention(event.target.checked)} /><span>Ich bestätige, dass die aufgeführten Altbestände nach der Umstellung gesperrt aufbewahrt werden und hierfür keine Freigaben erteilt werden.</span></label>
      {!allLoaded && <p>Vor der Entscheidung alle Einträge laden und prüfen.</p>}
      <button className="btn" type="button" disabled={busy || loading || !verified || !state?.acceptance_available || !allLoaded || !team || !retention || !reason.trim()} onClick={() => void accept()}>Altbestandsentscheidung speichern</button>
    </>}
    {saved && onDecisionSelected && <button className="btn" type="button" disabled={busy || loading || !verified || !state?.current || !allLoaded || !!state.blocker_count || !!state.active_work_count}
      onClick={() => { onDecisionSelected(saved); document.getElementById("zugriffsmodus")?.scrollIntoView({ behavior: "smooth" }); }}>Als Grundlage für die Umstellung auswählen</button>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}
