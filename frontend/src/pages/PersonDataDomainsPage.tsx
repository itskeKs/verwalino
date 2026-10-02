import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";
import { SearchChoice } from "../components/SearchChoice";

interface Domain { id: string; person_id: string; label: string; reason_code: ReasonCode; enforcement_status: "PREPARATION" }
interface Grant { id: string; user_display_name: string; capabilities: string[]; reason_code: ReasonCode; revoked_at: string | null; expires_at: string | null }
type ReasonCode = "INITIAL_SETUP" | "TEAM_ACCESS" | "TEMPORARY_ACCESS" | "REPLACEMENT";
const reasons: Record<ReasonCode, string> = { INITIAL_SETUP: "Ersteinrichtung", TEAM_ACCESS: "Teamzugriff", TEMPORARY_ACCESS: "Befristeter Zugriff", REPLACEMENT: "Ersatz einer Freigabe" };
interface Contact { id: string; first_name: string; last_name: string }
function ReasonChoice({ value, onChange }: { value: ReasonCode; onChange: (value: ReasonCode) => void }) {
  return <label>Begründung<select value={value} onChange={e => onChange(e.target.value as ReasonCode)}>{Object.entries(reasons).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label>;
}

interface Member { id: string; display_name: string; email: string }
const capabilities = { read: "Lesen", prepare: "Bearbeiten", export: "Exportieren" };

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
  const page = usePagedList<Domain>("/api/person-data-domains/page");
  const [person, setPerson] = useState<Contact | null>(null);
  const [reason, setReason] = useState<ReasonCode>("INITIAL_SETUP");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Domain | null>(null);
  const key = useRequestKey();
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!person) return; setBusy(true); setError(null);
    const body = { person_id: person.id, reason_code: reason };
    try { setCreated(await api.post<Domain>("/api/person-data-domains", body, { headers: key.headers(body) })); page.reload(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <>
    {created ? <p role="status">Datenbereich vorbereitet: <Link to={`/kontaktdaten-rechte/${created.id}`}>{created.label} – Teamfreigaben vorbereiten</Link></p> :
      <form className="card" onSubmit={submit}><h2>Datenbereich vorbereiten</h2>
        <p>Jeder Kontakt erhält einen eigenen Datenbereich. Die Zuordnung wird dauerhaft festgehalten; die Kennung enthält keinen Kontaktnamen.</p>
        <fieldset disabled={busy} className="form-grid" style={{ border: 0, minWidth: 0 }}>
          <SearchChoice endpoint="/api/persons/page" value={person} onChange={setPerson} label="Kontakt" display={item => `${item.first_name} ${item.last_name}`} required />
          <ReasonChoice value={reason} onChange={setReason} />
          <button className="btn btn--primary" disabled={!person || busy} type="submit">Datenbereich vorbereiten</button>
        </fieldset>
      </form>}
    {(error || page.error) && <p role="alert" className="error-text">{error || page.error}</p>}
    <ListSearch label="Datenbereiche suchen" onSearch={page.setQuery} />
    {page.loading && !page.items.length ? <p role="status">Lade Datenbereiche…</p> : <ul className="card">
      {page.items.map(domain => <li key={domain.id}><Link to={`/kontaktdaten-rechte/${domain.id}`}>{domain.label}</Link> · Vorbereitet</li>)}
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
  const [reason, setReason] = useState<ReasonCode>("TEAM_ACCESS");
  const [expiry, setExpiry] = useState("");
  const [busy, setBusy] = useState(false);
  const key = useRequestKey();
  const path = `/api/person-data-domains/${domainId}`;
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
    const body = { user_id: member.id, capabilities: [...selected].sort(), reason_code: reason, expires_at: expiry ? new Date(expiry).toISOString() : null };
    try { await api.post(path + "/grants", body, { headers: key.headers(body) }); key.reset(); setMember(null); setReason("TEAM_ACCESS"); page.reload(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function revoke(grant: Grant) {
    setBusy(true); setError(null);
    try { await api.post(path + `/grants/${grant.id}/revoke`); page.reload(); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  return <>
    <Link to="/kontaktdaten-rechte">Zurück zu den Datenbereichen</Link>
    {domain && <><h2>{domain.label}</h2><p>{reasons[domain.reason_code]} · <Link to={`/personen/${domain.person_id}`}>Kontakt anzeigen</Link></p></>}
    {detailError && <p role="alert" className="error-text">{detailError}</p>}
    <form className="card" onSubmit={submit}><h2>Teamfreigabe vorbereiten</h2>
      <fieldset disabled={busy || !domain} className="form-grid" style={{ border: 0, minWidth: 0 }}>
        <SearchChoice endpoint="/api/users/page?eligible_for_scope=true" value={member} onChange={setMember} label="Teammitglied" display={item => `${item.display_name} (${item.email})`} required />
        <div className="scope-capabilities">{Object.entries(capabilities).map(([value, caption]) => <label className="scope-check" key={value}>
          <input type="checkbox" checked={selected.includes(value)} disabled={value === "read"} onChange={e => setSelected(e.target.checked ? [...selected, value] : selected.filter(item => item !== value))} /> {caption}
        </label>)}</div>
        <ReasonChoice value={reason} onChange={setReason} />
        <label>Ablauf (optional, Ortszeit)<input type="datetime-local" value={expiry} onChange={e => setExpiry(e.target.value)} /></label>
        <button className="btn btn--primary" disabled={!member || busy} type="submit">Teamfreigabe vorbereiten</button>
      </fieldset>
    </form>
    {(error || page.error) && <p role="alert" className="error-text">{error || page.error}</p>}
    <div className="card paged-table"><table className="data-table"><thead><tr><th>Teammitglied</th><th>Rechte</th><th>Begründung</th><th>Status</th><th>Aktion</th></tr></thead><tbody>
      {page.items.map(grant => <tr key={grant.id}><td>{grant.user_display_name}</td><td>{grant.capabilities.map(cap => capabilities[cap as keyof typeof capabilities]).join(", ")}</td><td>{reasons[grant.reason_code]}</td>
        <td>{grant.revoked_at ? "Widerrufen" : grant.expires_at ? `Vorbereitet bis ${new Date(grant.expires_at).toLocaleString("de-DE")}` : "Vorbereitet ohne Ablauf"}</td>
        <td>{!grant.revoked_at && <button className="btn" disabled={busy} onClick={() => void revoke(grant)}>Widerrufen</button>}</td></tr>)}
    </tbody></table></div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </>;
}

export function PersonDataDomainsPage() {
  const { session } = useAuth();
  const { domainId } = useParams();
  if (session?.role !== "OWNER_ADMIN") return <p className="error-text">Zugriff nur für Inhaber/Admin.</p>;
  return <div className="paged-list scope-setup"><h1>Kontaktdaten-Rechte</h1>
    <p>Vorbereitung: Diese Freigaben beschränken den bisherigen Zugriff noch nicht. Die Zugriffskontrolle für Kontaktdaten ist noch nicht aktiviert.</p>
    <p>Objektfreigaben und Verwaltungsrollen gewähren keine automatische Kontaktfreigabe.</p>
    <p>Finanzielle Berechtigungen werden separat in den <Link to="/berechtigungen">Abrechnungsbereichen</Link> verwaltet.</p>
    {domainId ? <DomainGrants key={domainId} domainId={domainId} /> : <DomainList />}
  </div>;
}
