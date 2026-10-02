import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";
import { PropertySelector } from "../components/PropertySelector";
import { SearchChoice } from "../components/SearchChoice";

interface Domain { id: string; property_id: string; label: string; reason: string; enforcement_status: "PREPARATION" }
interface Grant { id: string; user_display_name: string; capabilities: string[]; reason: string; revoked_at: string | null; expires_at: string | null }
interface Member { id: string; display_name: string; email: string }
const capabilities = { read: "Lesen", prepare: "Bearbeiten", approve: "Freigeben", export: "Exportieren" };

function useRequestKey() {
  const saved = useRef({ body: "", key: "" });
  return {
    headers(body: unknown) {
      const serialized = JSON.stringify(body);
      if (serialized !== saved.current.body) saved.current = { body: serialized, key: crypto.randomUUID() };
      return { "Idempotency-Key": saved.current.key };
    },
    reset() { saved.current = { body: "", key: "" }; },
  };
}

function DomainList() {
  const page = usePagedList<Domain>("/api/property-data-domains/page");
  const [property, setProperty] = useState("");
  const [label, setLabel] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Domain | null>(null);
  const key = useRequestKey();
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    const body = { property_id: property, label, reason };
    try { setCreated(await api.post<Domain>("/api/property-data-domains", body, { headers: key.headers(body) })); page.reload(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <>
    {created ? <p role="status">Datenbereich vorbereitet: <Link to={`/gebaeudedaten-rechte/${created.id}`}>{created.label} – Teamfreigaben vorbereiten</Link></p> :
      <form className="card" onSubmit={submit}><h2>Datenbereich vorbereiten</h2>
        <p>Jedes Objekt erhält einen gemeinsamen Datenbereich. Die Zuordnung wird dauerhaft festgehalten.</p>
        <fieldset disabled={busy} className="form-grid" style={{ border: 0, minWidth: 0 }}>
          <PropertySelector value={property} onChange={setProperty} label="Objekt" required emptyLabel="Bitte wählen" />
          <label>Bezeichnung<input required maxLength={255} value={label} onChange={e => setLabel(e.target.value)} /></label>
          <label>Begründung<input required maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label>
          <button className="btn btn--primary" disabled={!property || busy} type="submit">Datenbereich vorbereiten</button>
        </fieldset>
      </form>}
    {(error || page.error) && <p role="alert" className="error-text">{error || page.error}</p>}
    <ListSearch label="Datenbereiche suchen" onSearch={page.setQuery} />
    {page.loading && !page.items.length ? <p role="status">Lade Datenbereiche…</p> : <ul className="card">
      {page.items.map(domain => <li key={domain.id}><Link to={`/gebaeudedaten-rechte/${domain.id}`}>{domain.label}</Link> · Vorbereitet</li>)}
      {!page.items.length && <li>Noch keine Datenbereiche vorbereitet.</li>}
    </ul>}
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </>;
}

function DomainGrants({ domainId }: { domainId: string }) {
  const [domain, setDomain] = useState<Domain | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [member, setMember] = useState<Member | null>(null);
  const [selected, setSelected] = useState<string[]>(["read"]);
  const [reason, setReason] = useState("");
  const [expiry, setExpiry] = useState("");
  const [busy, setBusy] = useState(false);
  const key = useRequestKey();
  const path = `/api/property-data-domains/${domainId}`;
  const page = usePagedList<Grant>(path + "/grants/page");
  useEffect(() => {
    const controller = new AbortController();
    api.get<Domain>(path, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) setDomain(result); })
      .catch(e => { if (!controller.signal.aborted) setDetailError(String(e)); });
    return () => controller.abort();
  }, [path]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!member) return;
    setBusy(true); setError(null);
    const body = { user_id: member.id, capabilities: [...selected].sort(), reason, expires_at: expiry ? new Date(expiry).toISOString() : null };
    try { await api.post(path + "/grants", body, { headers: key.headers(body) }); key.reset(); setMember(null); setReason(""); page.reload(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function revoke(grant: Grant) {
    setBusy(true); setError(null);
    try { await api.post(path + `/grants/${grant.id}/revoke`); page.reload(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <>
    <Link to="/gebaeudedaten-rechte">Zurück zu den Datenbereichen</Link>
    {domain && <><h2>{domain.label}</h2><p>{domain.reason} · <Link to={`/objekte/${domain.property_id}`}>Objekt anzeigen</Link></p></>}
    {detailError && <p role="alert" className="error-text">{detailError}</p>}
    <form className="card" onSubmit={submit}><h2>Teamfreigabe vorbereiten</h2>
      <fieldset disabled={busy || !domain} className="form-grid" style={{ border: 0, minWidth: 0 }}>
        <SearchChoice endpoint="/api/users/page?eligible_for_scope=true" value={member} onChange={setMember} label="Teammitglied" display={item => `${item.display_name} (${item.email})`} required />
        <div className="scope-capabilities">{Object.entries(capabilities).map(([value, caption]) => <label className="scope-check" key={value}>
          <input type="checkbox" checked={selected.includes(value)} disabled={value === "read"} onChange={e => setSelected(e.target.checked ? [...selected, value] : selected.filter(item => item !== value))} /> {caption}
        </label>)}</div>
        <label>Begründung<input required maxLength={2000} value={reason} onChange={e => setReason(e.target.value)} /></label>
        <label>Ablauf (optional, Ortszeit)<input type="datetime-local" value={expiry} onChange={e => setExpiry(e.target.value)} /></label>
        <button className="btn btn--primary" disabled={!member || busy} type="submit">Teamfreigabe vorbereiten</button>
      </fieldset>
    </form>
    {(error || page.error) && <p role="alert" className="error-text">{error || page.error}</p>}
    <div className="card paged-table"><table className="data-table"><thead><tr><th>Teammitglied</th><th>Rechte</th><th>Begründung</th><th>Status</th><th>Aktion</th></tr></thead><tbody>
      {page.items.map(grant => <tr key={grant.id}><td>{grant.user_display_name}</td><td>{grant.capabilities.map(cap => capabilities[cap as keyof typeof capabilities]).join(", ")}</td><td>{grant.reason}</td>
        <td>{grant.revoked_at ? "Widerrufen" : grant.expires_at ? `Vorbereitet bis ${new Date(grant.expires_at).toLocaleString("de-DE")}` : "Vorbereitet ohne Ablauf"}</td>
        <td>{!grant.revoked_at && <button className="btn" disabled={busy} onClick={() => void revoke(grant)}>Widerrufen</button>}</td></tr>)}
    </tbody></table></div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </>;
}

export function PropertyDataDomainsPage() {
  const { session } = useAuth();
  const { domainId } = useParams();
  if (session?.role !== "OWNER_ADMIN") return <p className="error-text">Zugriff nur für Inhaber/Admin.</p>;
  return <div className="paged-list scope-setup"><h1>Gemeinsame Gebäudedaten</h1>
    <p>Vorbereitung: Diese Freigaben beschränken den bisherigen Zugriff noch nicht. Die gemeinsame Zugriffskontrolle für Gebäudedaten und Quellen ist noch nicht aktiviert.</p>
    <p><Link to="/altquellen">Bereits zugeordnete Altquellen prüfen</Link></p>
    <p><Link to="/quellenfreigaben">Eigene Originale prüfen und für den Datenbereich freigeben</Link></p>
    <p>Finanzielle Berechtigungen werden separat in den <Link to="/berechtigungen">Abrechnungsbereichen</Link> verwaltet.</p>
    {domainId ? <DomainGrants key={domainId} domainId={domainId} /> : <DomainList />}
  </div>;
}
