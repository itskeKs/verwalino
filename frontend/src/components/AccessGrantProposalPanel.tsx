import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

export interface CoverageGrantItem {
  id: string; kind: string; resource_id: string; references: Record<string, unknown>;
  audience: {grant_id: string; user_id: string; capabilities: string[]; revoked_at?: string|null}[];
}
interface Change { domain_kind: string; audience_id: string; user_id: string; expected_grant_id: string|null; capabilities: string[]; expires_at: string|null }
interface Acceptance { id: string; accepted_at: string; results: {grant_id: string|null; revoked_grant_id: string|null}[] }
interface Proposal { id: string; changes: Change[]; proposal_digest: string; acceptance: Acceptance|null }
const labels: Record<string,string> = { financial:"Finanzdaten",property:"Gebäudedaten",person:"Kontaktdaten",read:"Lesen",prepare:"Vorbereiten",confirm:"Bestätigen",approve:"Freigeben",send:"Senden",export:"Exportieren" };
const allowed: Record<string,string[]> = { financial:["read","prepare","confirm","approve","send","export"],property:["read","prepare","approve","export"],person:["read","prepare","export"] };
function target(item: CoverageGrantItem) {
  if (item.kind==="accounting_scope") return {kind:"financial",id:item.resource_id};
  if (["property","person"].includes(item.kind) && typeof item.references.domain_id==="string") return {kind:item.kind,id:item.references.domain_id};
  return null;
}

export function AccessGrantProposalPanel({snapshotId,digest,items,complete,reviewed}: {
  snapshotId:string; digest:string; items:CoverageGrantItem[]; complete:boolean; reviewed:boolean;
}) {
  const [proposal,setProposal] = useState<Proposal|null>(null);
  const [loaded,setLoaded] = useState(false);
  const [reload,setReload] = useState(0);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState<string|null>(null);
  const [changes,setChanges] = useState<Change[]>([]);
  const [selected,setSelected] = useState("");
  const [member,setMember] = useState("");
  const [action,setAction] = useState("set");
  const [caps,setCaps] = useState<string[]>(["read"]);
  const [expiry,setExpiry] = useState("");
  const [ack,setAck] = useState(false);
  const request = useRef<{body:string;key:string}|null>(null);
  const acceptKey = useRef<string|null>(null);
  const inFlight = useRef(false);
  useEffect(()=>{
    let live = true;
    api.get<Proposal|null>(`/api/access-preparation/snapshots/${snapshotId}/grant-proposal`).then(row=>{
      if(live){setProposal(row);setLoaded(true);setError(null);}
    }).catch(()=>{if(live){setLoaded(false);setError("Gespeicherter Vorschlag nicht verfügbar. Bitte erneut laden.");}});
    return ()=>{live=false;};
  },[snapshotId,reload]);
  const entries = items.filter(item=>target(item));
  const members = items.filter(item=>item.kind==="member");
  const entry = entries.find(item=>item.id===selected);
  const audience = entry ? target(entry) : null;
  const person = members.find(item=>item.resource_id===member);
  const previous = entry?.audience.find(g=>g.user_id===member && !g.revoked_at);
  const choices = audience ? allowed[audience.kind].filter(cap=>["OWNER_ADMIN","CLERK"].includes(String(person?.references.role)) || cap==="read" || (audience.kind!=="financial" && cap==="export")) : [];
  function add(now: number) {
    if (!audience || !member || !person || changes.length>=100) return;
    if (changes.some(c=>c.domain_kind===audience.kind && c.audience_id===audience.id && c.user_id===member)) {
      setError("Für diesen Bereich und dieses Teammitglied ist bereits eine Änderung vorgemerkt.");return;
    }
    if (action==="revoke" && !previous) {setError("Es gibt keine bestehende Freigabe zum Widerrufen.");return;}
    if (action==="set" && (!person.references.active || !person.references.role)) {setError("Neue Freigaben benötigen ein aktives Teammitglied mit gültiger Mitgliedschaft.");return;}
    const until = expiry ? new Date(expiry) : null;
    if (action==="set" && until && (!Number.isFinite(until.getTime()) || until.getTime()<=now)) {setError("Das Ablaufdatum muss in der Zukunft liegen.");return;}
    setChanges([...changes,{domain_kind:audience.kind,audience_id:audience.id,user_id:member,expected_grant_id:previous?.grant_id ?? null,
      capabilities:action==="revoke" ? [] : caps.filter(c=>choices.includes(c)).sort(),expires_at:action==="revoke" ? null : until?.toISOString() ?? null}]);
    setError(null);
  }
  async function save() {
    if(inFlight.current || !loaded || !complete || !reviewed || !changes.length)return;
    const body = JSON.stringify({manifest_digest:digest,changes});
    if(request.current?.body!==body)request.current={body,key:crypto.randomUUID()};
    inFlight.current=true;setBusy(true);setError(null);
    try {setProposal(await api.post<Proposal>(`/api/access-preparation/snapshots/${snapshotId}/grant-proposals`,JSON.parse(body),{headers:{"Idempotency-Key":request.current.key}}));}
    catch(e){setError(e instanceof ApiError && e.status===409 ? "Der Prüfstand ist veraltet oder hat bereits einen Vorschlag. Gespeicherten Vorschlag laden oder einen neuen Prüfstand erfassen." : "Vorschlag nicht bestätigt. Erneut versuchen; derselbe Auftrag wird wiederverwendet.");}
    finally{inFlight.current=false;setBusy(false);}
  }
  async function accept() {
    if(inFlight.current || !loaded || !proposal || proposal.acceptance || !ack)return;
    acceptKey.current ??= crypto.randomUUID();inFlight.current=true;setBusy(true);setError(null);
    try{
      const receipt=await api.post<Acceptance>(`/api/access-preparation/grant-proposals/${proposal.id}/accept`,{proposal_digest:proposal.proposal_digest,acknowledged:true},{headers:{"Idempotency-Key":acceptKey.current}});
      setProposal({...proposal,acceptance:receipt});
    }catch(e){setError(e instanceof ApiError && e.status===409 ? "Die Berechtigungen oder Prüfdaten haben sich geändert. Neue Freigaben wurden nicht übernommen. Bitte einen neuen Prüfstand erfassen; bei unklarer vorheriger Antwort zuerst den gespeicherten Vorschlag laden." : "Annahme nicht bestätigt. Erneut versuchen; derselbe Auftrag wird wiederverwendet.");}
    finally{inFlight.current=false;setBusy(false);}
  }
  function summary(c:Change) {
    return <><p className="break-all">{labels[c.domain_kind]} · Bereich {c.audience_id} · Team {c.user_id}</p>
      <p>{c.capabilities.length ? `${c.expected_grant_id ? "Ersetzen" : "Neu erteilen"}: ${c.capabilities.map(cap=>labels[cap]).join(", ")}` : "Bestehende Freigabe widerrufen"}</p>
      <p>{c.expires_at ? `Gültig bis ${new Date(c.expires_at).toLocaleString("de-DE")}` : "Ohne Ablaufdatum"}</p>
      {c.expected_grant_id && <p className="break-all text-sm">Bisherige Freigabe: {c.expected_grant_id}</p>}</>;
  }
  return <section className="space-y-3" aria-label="Freigabevorschlag">
    <h3>Konkrete Teamfreigaben vorschlagen</h3>
    <p>Gebäude-, Kontakt- und Finanzdaten haben getrennte Leserkreise. Das Speichern erteilt keine Rechte. Die ausdrückliche Annahme setzt alle vorgeschlagenen Änderungen gemeinsam um.</p>
    <button className="btn" type="button" disabled={busy} onClick={()=>{setLoaded(false);setReload(v=>v+1);}}>Gespeicherten Vorschlag laden</button>
    {!loaded && !error && <p role="status">Vorschlag wird geladen…</p>}
    {proposal ? <>
      <ul className="space-y-2">{proposal.changes.map((c,i)=><li className="card space-y-1" key={i}>{summary(c)}</li>)}</ul>
      {proposal.acceptance ? <p role="status">Annahme gespeichert · {new Date(proposal.acceptance.accepted_at).toLocaleString("de-DE")}. Dieser Beleg dokumentiert die damalige Änderung. Spätere Widerrufe bleiben wirksam. Für die weitere Prüfung einen neuen Prüfstand erfassen.</p> : <>
        <label className="flex items-start gap-2"><input type="checkbox" checked={ack} disabled={busy} onChange={e=>setAck(e.target.checked)} /><span>Ich möchte genau diese Teamfreigaben jetzt erteilen, ersetzen oder widerrufen.</span></label>
        <button className="btn" disabled={busy || !loaded || !ack} onClick={()=>void accept()}>Freigabevorschlag verbindlich annehmen</button>
      </>}
    </> : loaded && <>
      {(!complete || !reviewed) && <p>Alle Einträge laden und den Prüfstand als geprüft vermerken, bevor Änderungen vorgeschlagen werden.</p>}
      <fieldset disabled={busy || !complete || !reviewed} className="space-y-3">
        <label className="block">Datenbereich<select className="block w-full border rounded-md p-2" value={selected} onChange={e=>{setSelected(e.target.value);setCaps(["read"]);}}><option value="">Bitte auswählen</option>{entries.map(item=>{const t=target(item)!;return <option key={item.id} value={item.id}>{labels[t.kind]} · {item.resource_id}</option>;})}</select></label>
        <label className="block">Teammitglied<select className="block w-full border rounded-md p-2" value={member} onChange={e=>{setMember(e.target.value);setCaps(["read"]);}}><option value="">Bitte auswählen</option>{members.map(item=><option key={item.id} value={item.resource_id}>{item.resource_id} · {String(item.references.role ?? "Ohne Rolle")}{item.references.active ? "" : " · inaktiv"}</option>)}</select></label>
        <label className="block">Änderung<select className="block w-full border rounded-md p-2" value={action} onChange={e=>setAction(e.target.value)}><option value="set">Freigabe erteilen oder ersetzen</option><option value="revoke">Freigabe widerrufen</option></select></label>
        {previous && <p>Bisher gespeichert: {previous.capabilities.map(cap=>labels[cap]).join(", ")}. Bei Ersetzung gelten ausschließlich die neu ausgewählten Rechte.</p>}
        {action==="set" && <><div className="flex flex-wrap gap-3">{choices.map(cap=><label className="flex gap-1" key={cap}><input type="checkbox" disabled={cap==="read"} checked={caps.includes(cap)} onChange={e=>setCaps(e.target.checked ? [...caps,cap] : caps.filter(c=>c!==cap))} />{labels[cap]}</label>)}</div>
          <label className="block">Ablaufdatum (optional)<input className="block w-full border rounded-md p-2" type="datetime-local" value={expiry} onChange={e=>setExpiry(e.target.value)} /></label></>}
        <button className="btn" type="button" disabled={!audience || !member || changes.length>=100} onClick={()=>add(Date.now())}>Änderung vormerken</button>
        <ul className="space-y-2">{changes.map((c,i)=><li className="card space-y-1" key={i}>{summary(c)}<button className="btn" type="button" onClick={()=>setChanges(changes.filter((_,n)=>n!==i))}>Änderung entfernen</button></li>)}</ul>
        <button className="btn" type="button" disabled={!changes.length} onClick={()=>void save()}>Vorschlag zur Prüfung speichern</button>
      </fieldset>
    </>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}
