import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { usePagedList } from "../hooks/usePagedList";
import { MoreResults } from "./ListSearch";

interface Review { id: string; reviewed_at: string; finding_count: number }
interface Snapshot { id: string; generated_at: string; manifest_digest: string; item_count: number; finding_count: number; review: Review | null }
interface Grant { grant_id: string; user_id: string; effective_capabilities: string[]; capabilities: string[] }
interface Item { id: string; kind: string; resource_id: string; references: Record<string, unknown>; findings: string[]; audience: Grant[] }

const findings: Record<string, string> = {
  DOMAIN_REQUIRED: "Ein ausdrücklicher Datenbereich fehlt.", LIVE_READER_REQUIRED: "Es gibt keine aktuelle Lesefreigabe.",
  HISTORY_GRAPH_INVALID: "Beziehungen in der Historie müssen geprüft werden.", MEETING_GRAPH_INVALID: "Versammlungsbezüge müssen geprüft werden.",
  QUARANTINED_INPUTS_BLOCK_OPERATIONS: "Quellen in Quarantäne können Berechnungen und Auskünfte blockieren.",
  SOURCE_DEPENDENCY_BLOCKS_OPERATIONS: "Abhängige Quellen haben keinen gültigen Freigabenachweis; Vorgänge bleiben blockiert.",
  SEV_FINANCIAL_ADAPTER_UNSUPPORTED: "Finanzvorgänge für diesen SEV-Bereich sind noch nicht unterstützt.",
  FINANCIAL_BINDING_REQUIRED: "Eine geprüfte Zuordnung zu einem Abrechnungskreis fehlt.",
  REVIEWED_QUARANTINE: "Die weitere Quarantäne wurde ausdrücklich dokumentiert.",
  VALID_BOUND_PROOF_REQUIRED: "Der zugeordneten Quelle fehlt ein aktuell gültiger Herkunftsnachweis.",
  UNKNOWN_CUSTODY: "Die ursprüngliche Zuständigkeit ist unbekannt und wird nicht nachgetragen.",
  MULTIPLE_IMPORT_CONTEXTS: "Mehrere Importbezüge benötigen eine gesonderte Prüfung.",
  SOURCE_AUDIENCE_REVIEW_REQUIRED: "Der Leserkreis dieser Quelle muss geprüft werden.",
  IMPORT_AUDIENCE_REVIEW_REQUIRED: "Der Leserkreis dieses Imports muss geprüft werden.",
  DERIVED_AUDIENCE_REVIEW_REQUIRED: "Die Freigaben der zugrunde liegenden Quellen müssen geprüft werden.",
  LIVE_CREATOR_REQUIRED: "Der private Ersteller hat keine aktive Mitgliedschaft.",
  WORK_IN_PROGRESS: "Eine Verarbeitung ist noch offen.", QUARANTINE_REVIEW_REQUIRED: "Die weitere Quarantäne muss geprüft werden.",
  DUNNING_CONTEXT_REVIEW_REQUIRED: "Mahnungsursprung oder weitere Quarantäne müssen geprüft werden.",
  EXPLICIT_TEAM_ASSIGNMENT_REVIEW: "Vorgesehene Teamaufgaben mit den ausdrücklichen Freigaben abgleichen.",
  INACTIVE_PRINCIPAL: "Inaktives Konto oder fehlende Mitgliedschaft; keine wirksame Freigabe.",
  BUDGET_POLICY_REVIEW_REQUIRED: "Die geprüfte Budgetregel für Wissensauskünfte fehlt.",
  BUDGET_POLICY_CONFIGURATION_MISMATCH: "Budgetregel und aktuelle Konfiguration stimmen nicht überein.",
  PARENT_CONTEXT_REVIEW_REQUIRED: "Übergeordnete Quellen und gemeinsame Zugriffsrechte ausdrücklich prüfen; ihre Vollständigkeit ist hier noch nicht bestätigt.",
  RECIPIENT_ACCESS_REVIEW_REQUIRED: "Finanzfreigabe und Zugriff auf den Zahlungsempfänger gemeinsam prüfen.",
  PROCESSING_PRINCIPAL_REVIEW_REQUIRED: "Die Verarbeitung hat keinen bekannten aktiven Bearbeiter mit Schreibrolle.",
  PARENT_PROPERTY_MISMATCH: "Der angegebene Objektbezug stimmt nicht mit dem übergeordneten Vorgang überein.",
  INVALID_REFERENCE_ARRAY: "Eine historische Quellenliste enthält ungültige Verweise und muss gesondert geprüft werden.",
};
const kinds: Record<string,string> = { property:"Objekt", person:"Kontakt", accounting_scope:"Abrechnungskreis", member:"Teammitglied",
  bank_account:"Objektkonto", payment:"Zahlung", cost_entry:"Kosten", settlement_run:"Abrechnung", economic_plan:"Wirtschaftsplan",
  property_document:"Gebäudeoriginal", declaration_import:"Import", invoice_document:"Rechnungsoriginal", mandate_document:"Mandatsunterlage",
  dunning_notice:"Mahnung", knowledge_budget_policy:"Budgetregel", unit:"Einheit", property_rule:"Regel", approved_fact_source:"Bestätigte Quelle",
  management_mandate:"Verwaltungsmandat", owner_profile:"Eigentümerprofil", tenant_profile:"Mieterprofil", vendor:"Dienstleister",
  ownership_history:"Eigentümerhistorie", lease_agreement:"Mietvertrag", settlement_line:"Abrechnungsposition", economic_plan_line:"Wirtschaftsplanposition",
  coverage_declaration:"Bestätigung der Datengrundlage", owners_meeting:"Eigentümerversammlung", agenda_item:"Tagesordnungspunkt",
  resolution:"Beschluss", resolution_register_entry:"Eintrag in der Beschlusssammlung", declaration_import_document:"Import-Originalbezug",
  declaration_queue_request:"Verarbeitungsauftrag", declaration_candidate:"Importvorschlag", declaration_candidate_evidence:"Belegstelle eines Importvorschlags",
  declaration_candidate_issue:"Prüfpunkt eines Imports", declaration_extraction_run:"Extraktionslauf", declaration_import_pass:"Verarbeitungsschritt",
  invoice_processing_attempt:"Belegverarbeitung", property_knowledge_query_run:"Wissensauskunft", property_legal_reference:"Rechtsgrundlage",
  property_assignment:"Nutzungszuordnung", property_document_relation:"Dokumentbezug", property_rule_relation:"Regelbezug", property_rule_source:"Regelquelle" };
const capabilities: Record<string,string> = { read:"Lesen", prepare:"Vorbereiten", confirm:"Bestätigen", approve:"Freigeben", send:"Senden", export:"Exportieren" };

function proposal(kind: string): [string,string] | null {
  if (["property","unit"].includes(kind)) return ["/gebaeudedaten-rechte","Datenbereich und Teamfreigaben prüfen"];
  if (kind === "person") return ["/kontaktdaten-rechte","Kontaktdaten-Freigaben prüfen"];
  if (kind === "property_document") return ["/altquellen","Herkunft und Quarantäne prüfen"];
  if (kind === "declaration_import") return ["/quellenfreigaben","Quellenfreigaben prüfen"];
  if (kind === "mandate_document") return ["/mandatsquarantaene","Mandatsquarantäne prüfen"];
  if (kind === "dunning_notice") return ["/mahnquarantaene","Mahnungsquarantäne prüfen"];
  if (["accounting_scope","bank_account","payment","cost_entry","settlement_run","economic_plan","member"].includes(kind)) return ["/berechtigungen","Abrechnungskreise und Teamfreigaben prüfen"];
  return null;
}

function SnapshotDetail({ snapshot, onReviewed }: { snapshot: Snapshot; onReviewed: () => void }) {
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
      return <article className="card min-w-0 space-y-2" key={item.id}>
        <h4>{kinds[item.kind] ?? "Quellenbezug"}</h4><p className="break-all text-sm">Kennung: {item.resource_id}</p>
        {item.findings.length ? <ul className="list-disc pl-5">{item.findings.map(code => <li key={code}>{findings[code] ?? "Dieser Eintrag benötigt eine gesonderte Prüfung."}</li>)}</ul>
          : <p>Keine strukturelle Prüfhilfe für diesen Eintrag. Die vorgesehenen Teamaufgaben und abhängigen Quellen weiterhin ausdrücklich prüfen.</p>}
        {item.audience.map(grant => <p key={grant.grant_id} className="break-words text-sm">Teamkennung {grant.user_id}: {grant.effective_capabilities.map(cap => capabilities[cap] ?? cap).join(", ") || "Keine wirksame Berechtigung"}</p>)}
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
  </section>;
}

export function AccessCoveragePanel() {
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
    <p>Erfasst Kennungen, wirksame Freigaben und Prüfhilfen. Quelleninhalte werden hier nicht angezeigt. Die Aktivierung bleibt gesperrt.</p>
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
    {active && <SnapshotDetail key={active.id} snapshot={active} onReviewed={history.reload} />}
  </section>;
}
