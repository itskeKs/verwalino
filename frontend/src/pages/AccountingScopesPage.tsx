import { ScopeControl } from "../components/ScopeControl";
import { ScopePaymentReconciliation } from "../components/ScopePaymentReconciliation";
import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";
import { PropertySelector } from "../components/PropertySelector";
import { SearchChoice } from "../components/SearchChoice";

interface Scope { id: string; property_id: string; label: string; scope_type: string; whole_property: boolean; valid_from: string; valid_to: string | null }
interface Grant { id: string; user_id: string; user_display_name: string; capabilities: string[]; reason: string; revoked_at: string | null; expires_at: string | null }
interface User { id: string; display_name: string; email: string }
interface Mandate { id: string; label: string; mandate_type: string }
interface Unit { id: string; number: string }
interface Owner { id: string; display_name: string }
interface Selection { unit: Unit; owner: Owner | null }
const caps = { read: "Lesen", prepare: "Vorbereiten", confirm: "Bestätigen", approve: "Freigeben", send: "Versenden", export: "Exportieren" };

function useCommandKey() {
  const previous = useRef({ payload: "", key: "" });
  const headers = (payload: unknown) => {
    const serialized = JSON.stringify(payload);
    if (serialized !== previous.current.payload) previous.current = { payload: serialized, key: crypto.randomUUID() };
    return { "Idempotency-Key": previous.current.key };
  };
  return { headers, reset: () => { previous.current = { payload: "", key: "" }; } };
}

function UnitSelection({ propertyId, selections, onChange, sev }: {
  propertyId: string; selections: Selection[]; onChange: (items: Selection[]) => void; sev: boolean;
}) {
  const [unit, setUnit] = useState<Unit | null>(null);
  const [owner, setOwner] = useState<Owner | null>(null);
  return <div>
    <SearchChoice endpoint={`/api/units/page?property_id=${propertyId}`} value={unit} onChange={setUnit} label="Einheit" display={item => item.number} />
    {sev && <SearchChoice endpoint="/api/owner-profiles/page" value={owner} onChange={setOwner} label="Geprüfter Eigentümer" display={item => item.display_name} />}
    <button className="btn" type="button" disabled={!unit || (sev && !owner) || selections.some(s => s.unit.id === unit?.id)} onClick={() => {
      if (unit) { onChange([...selections, { unit, owner: sev ? owner : null }]); setUnit(null); setOwner(null); }
    }}>Einheit aufnehmen</button>
    <ul>{selections.map(item => <li key={item.unit.id}>{item.unit.number}{item.owner ? ` · ${item.owner.display_name}` : ""} <button className="btn" type="button" onClick={() => onChange(selections.filter(s => s.unit.id !== item.unit.id))}>Entfernen</button></li>)}</ul>
  </div>;
}

function CreateScope({ onCreated }: { onCreated: () => void }) {
  const [property, setProperty] = useState("");
  const [kind, setKind] = useState("WEG");
  const [label, setLabel] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [whole, setWhole] = useState(true);
  const [mandate, setMandate] = useState<Mandate | null>(null);
  const [units, setUnits] = useState<Selection[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Scope | null>(null);
  const key = useCommandKey();
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    const body = { property_id: property, management_mandate_id: mandate?.id ?? null, scope_type: kind, label,
      valid_from: start, valid_to: end || null, whole_property: whole,
      units: whole ? [] : units.map(item => ({ unit_id: item.unit.id, owner_profile_id: item.owner?.id ?? null })) };
    try { setCreated(await api.post<Scope>("/api/accounting-scopes", body, { headers: key.headers(body) })); onCreated(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  if (created) return <p role="status">Bereich angelegt: <Link to={`/berechtigungen/${created.id}`}>{created.label} – Teamrechte verwalten</Link></p>;
  return <form onSubmit={submit} className="card">
    <h2>Bereich anlegen</h2>
    <p>Objekt, Zeitraum und Einheiten werden verbindlich festgelegt. Teamrechte erteilen Sie anschließend ausdrücklich.</p>
    <fieldset className="form-grid" disabled={busy} style={{ border: 0, minWidth: 0 }}>
      <PropertySelector value={property} onChange={id => { setProperty(id); setUnits([]); }} label="Objekt" required emptyLabel="Bitte wählen" />
      <label>Bezeichnung<input required maxLength={255} value={label} onChange={e => setLabel(e.target.value)} /></label>
      <label>Verwaltungsart<select aria-label="Verwaltungsart" value={kind} onChange={e => { setKind(e.target.value); setUnits([]); if (e.target.value === "SEV") setWhole(false); }}><option>WEG</option><option>MIET</option><option>SEV</option></select></label>
      <SearchChoice endpoint="/api/mandates/page" value={mandate} onChange={setMandate} label="Mandat (optional)" display={item => `${item.label} (${item.mandate_type})`} />
      <label>Gültig ab<input required type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
      <label>Gültig bis (optional)<input type="date" min={start} value={end} onChange={e => setEnd(e.target.value)} /></label>
      <label className="scope-check"><input type="checkbox" checked={whole} disabled={kind === "SEV"} onChange={e => { setWhole(e.target.checked); setUnits([]); }} /> Gesamtes Objekt</label>
      {!whole && property && <UnitSelection key={property + kind} propertyId={property} selections={units} onChange={setUnits} sev={kind === "SEV"} />}
      <button className="btn btn--primary" type="submit" disabled={!property || (!whole && !units.length)}>{busy ? "Speichert…" : "Bereich verbindlich anlegen"}</button>
    </fieldset>
    {error && <p role="alert" className="error-text">{error}</p>}
  </form>;
}

function ScopeList() {
  const page = usePagedList<Scope>("/api/accounting-scopes/page");
  return <>
    <CreateScope onCreated={page.reload} />
    <ListSearch label="Bereiche suchen" onSearch={page.setQuery} />
    {page.error && <p role="alert" className="error-text">{page.error}</p>}
    <div className="card paged-table"><table className="data-table"><thead><tr><th>Bereich</th><th>Art</th><th>Gültigkeit</th></tr></thead><tbody>
      {page.items.map(item => <tr key={item.id}><td><Link to={`/berechtigungen/${item.id}`}>{item.label}</Link></td><td>{item.scope_type}</td><td>{item.valid_from} – {item.valid_to || "offen"}</td></tr>)}
    </tbody></table></div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </>;
}

function ScopeGrants({ scopeId }: { scopeId: string }) {
  const [scope, setScope] = useState<Scope | null>(null);
  const [scopeError, setScopeError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api.get<Scope>(`/api/accounting-scopes/${scopeId}`, { signal: controller.signal })
      .then(item => { if (!controller.signal.aborted) setScope(item); })
      .catch(error => { if (!controller.signal.aborted) setScopeError(String(error)); });
    return () => controller.abort();
  }, [scopeId]);
  const page = usePagedList<Grant>(`/api/accounting-scopes/${scopeId}/grants/page`);
  const [user, setUser] = useState<User | null>(null);
  const [selected, setSelected] = useState<string[]>(["read"]);
  const [reason, setReason] = useState("");
  const [expiry, setExpiry] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = useCommandKey();
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!user) return; setBusy(true); setError(null);
    const body = { user_id: user.id, capabilities: [...selected].sort(), reason, expires_at: expiry ? new Date(expiry).toISOString() : null };
    try { await api.post(`/api/accounting-scopes/${scopeId}/grants`, body, { headers: key.headers(body) }); page.reload(); key.reset(); setUser(null); setReason(""); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function revoke(grant: Grant) {
    setBusy(true); setError(null);
    try { await api.post(`/api/accounting-scopes/${scopeId}/grants/${grant.id}/revoke`); page.reload(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <>
    <Link to="/berechtigungen">Zurück zu den Bereichen</Link>
    {scope && <><h2>{scope.label} ({scope.scope_type})</h2><p>Gültig {scope.valid_from} bis {scope.valid_to || "offen"} · <Link to={`/objekte/${scope.property_id}`}>Objekt anzeigen</Link></p></>}
    {scopeError && <p role="alert" className="error-text">{scopeError}</p>}
    <form className="card" onSubmit={submit}><h2>Teamfreigabe erteilen</h2>
      <fieldset className="form-grid" disabled={busy || !scope} style={{ border: 0, minWidth: 0 }}>
        <SearchChoice endpoint="/api/users/page?eligible_for_scope=true" value={user} onChange={setUser} label="Teammitglied" display={item => `${item.display_name} (${item.email})`} required />
        <div className="scope-capabilities">{Object.entries(caps).map(([value, caption]) => <label className="scope-check" key={value}><input type="checkbox" checked={selected.includes(value)} disabled={value === "read"} onChange={e => setSelected(e.target.checked ? [...selected, value] : selected.filter(item => item !== value))} /> {caption}</label>)}</div>
        <label>Begründung<input required maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <label>Ablauf (optional, Ortszeit)<input type="datetime-local" value={expiry} onChange={e => setExpiry(e.target.value)} /></label>
        <button className="btn btn--primary" type="submit">Freigabe erteilen</button>
      </fieldset>
    </form>
    {(error || page.error) && <p role="alert" className="error-text">{error || page.error}</p>}
    <div className="card paged-table"><table className="data-table"><thead><tr><th>Teammitglied</th><th>Rechte</th><th>Begründung</th><th>Status</th><th>Aktion</th></tr></thead><tbody>
      {page.items.map(grant => <tr key={grant.id}><td>{grant.user_display_name}</td><td>{grant.capabilities.map(value => caps[value as keyof typeof caps]).join(", ")}</td><td>{grant.reason}</td><td>{grant.revoked_at ? "Widerrufen" : grant.expires_at ? `Erteilt bis ${new Date(grant.expires_at).toLocaleString("de-DE")}` : "Erteilt ohne Ablauf"}</td><td>{!grant.revoked_at && <button className="btn" disabled={busy} onClick={() => void revoke(grant)}>Widerrufen</button>}</td></tr>)}
    </tbody></table></div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
    {scope && <ScopePaymentReconciliation scopeId={scopeId} />}
  </>;
}

export function AccountingScopesPage() {
  const { session } = useAuth();
  const { scopeId } = useParams();
  if (session?.role !== "OWNER_ADMIN") return <p className="error-text">Zugriff nur für Inhaber/Admin.</p>;
  return <div className="paged-list scope-setup"><h1>Bereiche und Teamrechte</h1>
    <p>Vorbereitung: Die Zugriffsbeschränkung für den Teambetrieb ist noch nicht aktiviert. SEV-Bereiche können vorbereitet, Finanzdaten dort noch nicht zugeordnet werden.</p>
    <ScopeControl />
    {scopeId ? <ScopeGrants key={scopeId} scopeId={scopeId} /> : <ScopeList />}
  </div>;
}
