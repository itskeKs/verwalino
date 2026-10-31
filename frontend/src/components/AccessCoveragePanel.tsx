import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { usePagedList } from "../hooks/usePagedList";
import { AccessGrantProposalPanel } from "./AccessGrantProposalPanel";
import type { ActivationDecision } from "./SourceSecurityPanel";
import { AccessDispositionPanel } from "./AccessDispositionPanel";
import { coverageFindings as findings, coverageKinds as kinds } from "./accessCoverageLabels";
import { MoreResults } from "./ListSearch";

interface Review { id: string; reviewed_at: string; finding_count: number }
interface Snapshot { id: string; generated_at: string; manifest_digest: string; item_count: number; finding_count: number; review: Review | null }
interface Grant { grant_id: string; user_id: string; effective_capabilities: string[]; capabilities: string[] }
interface Item { id: string; kind: string; resource_id: string; references: Record<string, unknown>; findings: string[]; audience: Grant[] }

const capabilities: Record<string,string> = { read:"Lesen", prepare:"Vorbereiten", confirm:"Bestätigen", approve:"Freigeben", send:"Senden", export:"Exportieren" };

interface StrictAudience { user_id: string; capabilities: string[] }
function strictAudience(value: unknown): StrictAudience[] | null {
  if (!Array.isArray(value)) return null;
  return value.filter((row): row is StrictAudience => row !== null && typeof row === "object"
    && typeof row.user_id === "string" && Array.isArray(row.capabilities)
    && row.capabilities.every((cap: unknown) => typeof cap === "string"));
}

function proposal(kind: string): [string,string] | null {
  if (["property","unit"].includes(kind)) return ["/gebaeudedaten-rechte","Datenbereich und Teamfreigaben prüfen"];
  if (kind === "person") return ["/kontaktdaten-rechte","Kontaktdaten-Freigaben prüfen"];
  if (kind === "property_document") return ["/altquellen","Herkunft und Quarantäne prüfen"];
  if (kind === "declaration_import") return ["/berechtigungen/pruefungen/quellen","Quellenfreigaben prüfen"];
  if (kind === "mandate_document") return ["/berechtigungen/pruefungen/mandate","Mandatsquarantäne prüfen"];
  if (kind === "dunning_notice") return ["/berechtigungen/pruefungen/mahnungen","Mahnungsquarantäne prüfen"];
  if (["accounting_scope","bank_account","payment","cost_entry","settlement_run","economic_plan","member"].includes(kind)) return ["/berechtigungen/teamrechte","Abrechnungskreise und Teamfreigaben prüfen"];
  return null;
}

function SnapshotDetail({ snapshot, onReviewed, onDecisionSelected }: { snapshot: Snapshot; onReviewed: () => void; onDecisionSelected?: (decision: ActivationDecision) => void }) {
  const page = usePagedList<Item>(`/api/access-preparation/snapshots/${snapshot.id}/items`);
  const [acknowledged,setAcknowledged] = useState(false);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState<string|null>(null);
  const [receipt,setReceipt] = useState<Review|null>(null);
  const request = useRef<string|null>(null);
  const inFlight = useRef(false);
  const reviewed = snapshot.review ?? receipt;
  async function review() {
    if (inFlight.current || !acknowledged || reviewed) return;
    request.current ??= crypto.randomUUID();
    inFlight.current = true; setBusy(true); setError(null);
    try {
      setReceipt(await api.post<Review>(`/api/access-preparation/snapshots/${snapshot.id}/review`,
        { manifest_digest:snapshot.manifest_digest, acknowledged:true }, { headers:{"Idempotency-Key":request.current} }));
      onReviewed();
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status===409 ? "Der Prüfstand ist veraltet oder wurde bereits anders geprüft. Bitte einen neuen Stand erfassen." : "Prüfvermerk nicht bestätigt. Erneut versuchen; derselbe Auftrag wird wiederverwendet.");
    } finally { inFlight.current = false; setBusy(false); }
  }
  return <section className="space-y-4" aria-label="Gespeicherter Prüfstand">
    <h3>Stand vom {new Date(snapshot.generated_at).toLocaleString("de-DE")}</h3>
    <p>{snapshot.item_count} Einträge · {snapshot.finding_count} Einträge mit Prüfhilfen oder offenen Punkten</p>
    <p>Dieser gespeicherte Stand ändert sich nicht. Freigaben gelten nur entsprechend ihrer aktuellen Gültigkeit. Ein Prüfvermerk löst keine offenen Punkte und aktiviert keine Zugriffsregeln.</p>
    <details><summary>Prüfkennung</summary><code className="block break-all">{snapshot.manifest_digest}</code></details>
    <button className="btn" type="button" disabled={page.loading} onClick={page.reload}>Einträge erneut laden</button>
    {page.loading && <p role="status">Prüfeinträge werden geladen…</p>}
    {page.error && <p role="alert" className="error-text">Prüfeinträge nicht verfügbar. Bitte erneut laden.</p>}
    <div className="space-y-3">{page.items.map(item => {
      const next = proposal(item.kind);
      const projected = strictAudience(item.references.strict_audience);
      return <article className="card min-w-0 space-y-2" key={item.id}>
        <h4>{kinds[item.kind] ?? "Quellenbezug"}</h4><p className="break-all text-sm">Kennung: {item.resource_id}</p>
        {item.findings.length ? <ul className="list-disc pl-5">{item.findings.map(code => <li key={code}>{findings[code] ?? "Dieser Eintrag benötigt eine gesonderte Prüfung."}</li>)}</ul>
          : <p>Keine offenen Befunde in dieser Prüfung. Aktuelle Rechte und fachliche Voraussetzungen werden beim Vorgang erneut geprüft.</p>}
        {item.audience.map(grant => <p key={grant.grant_id} className="break-words text-sm">Teamkennung {grant.user_id}: {grant.effective_capabilities.map(cap => capabilities[cap] ?? cap).join(", ") || "Keine wirksame Berechtigung"}</p>)}
        {projected && <section className="space-y-1" aria-label="Gemeinsam geprüfte Berechtigungen">
          <h5>Gemeinsam geprüfte Berechtigungen · Vorschau</h5>
          <p className="text-sm">Berücksichtigt die benötigten Quellen, Kontakte und Abrechnungskreise je Teammitglied. Diese Vorschau erteilt keine Freigaben.</p>
          {projected.length ? projected.map(row => <p key={row.user_id} className="break-words text-sm">Teamkennung {row.user_id}: {row.capabilities.map(cap => capabilities[cap] ?? cap).join(", ")}</p>)
            : <p className="text-sm">Keine gemeinsam ausreichenden Berechtigungen.</p>}
        </section>}
        <details><summary>Zuordnungen und Freigabekennungen</summary><pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify({ references:item.references, grants:item.audience },null,2)}</pre></details>
        {next && <Link className="link" to={next[0]}>{next[1]}</Link>}
      </article>;
    })}</div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
    {reviewed ? <p role="status">Prüfvermerk gespeichert · {new Date(reviewed.reviewed_at).toLocaleString("de-DE")} · Offene Punkte bleiben bestehen.</p>
      : <div className="space-y-3">
        <label className="flex items-start gap-2"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={event=>setAcknowledged(event.target.checked)} />
          <span>Ich habe diesen Stand einschließlich Teamfreigaben und offener Punkte geprüft. Dies erteilt keine neuen Berechtigungen.</span></label>
        <button className="btn" type="button" disabled={!acknowledged || busy || page.loading || !!page.error || !!page.nextCursor} onClick={()=>void review()}>Stand als geprüft vermerken</button>
        {page.nextCursor && <p>Vor dem Prüfvermerk alle Einträge laden.</p>}
      </div>}
    {error && <p role="alert" className="error-text">{error}</p>}
    <AccessDispositionPanel snapshotId={snapshot.id} onDecisionSelected={onDecisionSelected} />
    <AccessGrantProposalPanel snapshotId={snapshot.id} digest={snapshot.manifest_digest} items={page.items} complete={!page.loading && !page.error && !page.nextCursor} reviewed={!!reviewed} />
  </section>;
}

export function AccessCoveragePanel({ onDecisionSelected }: { onDecisionSelected?: (decision: ActivationDecision) => void }) {
  const history = usePagedList<Snapshot>("/api/access-preparation/snapshots");
  const [selected,setSelected] = useState<string|null>(null);
  const [created,setCreated] = useState<Snapshot|null>(null);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState<string|null>(null);
  const request = useRef<{key:string; predecessor:string|null}|null>(null);
  const inFlight = useRef(false);
  const active = history.items.find(item=>item.id===selected) ?? (created?.id===selected ? created : null);
  async function capture() {
    if (inFlight.current) return;
    if (!request.current || request.current.predecessor!==selected) request.current = {key:crypto.randomUUID(),predecessor:selected};
    inFlight.current = true; setBusy(true); setError(null);
    try {
      const result = await api.post<Snapshot>("/api/access-preparation/snapshots", {predecessor_id:request.current.predecessor}, {headers:{"Idempotency-Key":request.current.key}});
      setCreated({...result,review:null}); setSelected(result.id); request.current = null; history.reload();
    } catch { setError("Prüfstand nicht bestätigt. Erneut versuchen; derselbe Auftrag wird wiederverwendet."); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <section className="space-y-4" aria-label="Versionierte Prüfstände">
    <h2>Prüfstand mit konkreten Zuordnungen</h2>
    <p>Erfasst Kennungen, wirksame Freigaben und Prüfhilfen. Quelleninhalte werden hier nicht angezeigt. Das Erfassen und Prüfen eines Stands allein aktiviert keine Zugriffsregeln.</p>
    <div className="flex flex-wrap gap-2"><button className="btn" type="button" disabled={busy} onClick={()=>void capture()}>Neuen Prüfstand erfassen</button>
      <button className="btn" type="button" disabled={history.loading} onClick={history.reload}>Gespeicherte Prüfstände laden</button></div>
    {error && <p role="alert" className="error-text">{error}</p>}
    {history.loading && <p role="status">Prüfstände werden geladen…</p>}
    {history.error && <p role="alert" className="error-text">Gespeicherte Prüfstände nicht verfügbar. Bitte erneut laden.</p>}
    {history.items.length>0 && <label className="block">Gespeicherten Stand auswählen
      <select className="block w-full rounded-md border p-2" disabled={busy} value={selected ?? ""} onChange={event=>{setSelected(event.target.value || null);setError(null);}}>
        <option value="">Bitte auswählen</option>{history.items.map(row=><option key={row.id} value={row.id}>{new Date(row.generated_at).toLocaleString("de-DE")} · {row.item_count} Einträge{row.review ? " · geprüft" : ""}</option>)}
      </select></label>}
    <MoreResults available={!!history.nextCursor} loading={history.loading} onMore={()=>void history.loadMore()} />
    {active && <SnapshotDetail key={active.id} snapshot={active} onDecisionSelected={onDecisionSelected} onReviewed={history.reload} />}
  </section>;
}
