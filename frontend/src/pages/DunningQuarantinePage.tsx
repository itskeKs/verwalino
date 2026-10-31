import { useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { MoreResults } from "../components/ListSearch";

type Reason = "SOURCE_CONTEXT_UNVERIFIED" | "MIXED_SCOPE_HISTORY";
interface Disposition { id: string; notice_id: string; snapshot_digest: string; reason: Reason; reviewed_by: string; reviewed_at: string }
interface Observation { id: string; notice_id: string; snapshot_digest: string; observed_at: string; disposition: Disposition | null }
const reasons: Record<Reason, string> = {
  SOURCE_CONTEXT_UNVERIFIED: "Abrechnungskontext weiterhin nicht belegt",
  MIXED_SCOPE_HISTORY: "Historie lässt sich keinem eindeutigen Bereich zuordnen",
};

function ObservationCard({ row }: { row: Observation }) {
  const [reason, setReason] = useState<Reason>("SOURCE_CONTEXT_UNVERIFIED");
  const [confirmed, setConfirmed] = useState(false);
  const [savedReceipt, setReceipt] = useState<Disposition | null>(null);
  const receipt = row.disposition ?? savedReceipt;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inFlight = useRef(false);
  const request = useRef<{ fingerprint: string; key: string } | null>(null);
  async function review(event: React.FormEvent) {
    event.preventDefault();
    if (inFlight.current || !confirmed || receipt) return;
    const body = { notice_id: row.notice_id, snapshot_digest: row.snapshot_digest, reason };
    const fingerprint = JSON.stringify(body);
    if (request.current?.fingerprint !== fingerprint) request.current = { fingerprint, key: crypto.randomUUID() };
    inFlight.current = true; setBusy(true); setError(null);
    try {
      setReceipt(await api.post<Disposition>("/api/dunning-quarantine", body, { headers: { "Idempotency-Key": request.current.key } }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Prüfvermerk konnte nicht gespeichert werden.");
    } finally { inFlight.current = false; setBusy(false); }
  }
  return <section className="card min-w-0 space-y-3">
    <h2 className="break-all">Mahnung {row.notice_id}</h2>
    <p>Metadaten beobachtet am {new Date(row.observed_at).toLocaleString("de-DE")}</p>
    <details><summary>Prüfkennung anzeigen</summary><code className="block break-all">{row.snapshot_digest}</code></details>
    {receipt ? <p role="status">Quarantäne geprüft · {reasons[receipt.reason]} · {new Date(receipt.reviewed_at).toLocaleString("de-DE")}</p>
      : <form onSubmit={event => void review(event)}>
        <fieldset disabled={busy} className="space-y-3 border-0 p-0">
          <label className="block">Grund für die weitere Quarantäne
            <select className="block w-full min-w-0 rounded-md border p-2" value={reason} onChange={event => setReason(event.target.value as Reason)}>
              {Object.entries(reasons).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
            <span>Ich bestätige die weitere Quarantäne. Dies erteilt keine Freigabe und bestätigt keinen historischen Abrechnungskontext.</span>
          </label>
          <button className="btn" type="submit" disabled={busy || !confirmed}>Quarantäne als geprüft vermerken</button>
        </fieldset>
      </form>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}

function Inventory() {
  const { pathname } = useLocation();
  const page = usePagedList<Observation>("/api/dunning-quarantine/page");
  return <div className="space-y-5">
    <header><h2>Alte Mahnungen prüfen</h2><p>Administrative Prüfung alter Mahnungen ohne belegten Abrechnungskontext. Es werden nur Kennungen und Prüfstatus angezeigt.</p></header>
    <p>Der Prüfvermerk hält den aktuellen Stand unverändert in Quarantäne. Er ergänzt keinen Abrechnungskontext und kann nicht zurückgenommen werden. Die Mahnung bleibt für die Prüfung bisheriger Mahnstufen und der Aufbewahrung berücksichtigt.</p>
    <p>Neue <Link to="/mahnungen" className="link">Mahnungen</Link> benötigen geprüfte Quellen und einen ausdrücklichen Abrechnungskontext. Eine ungeklärte Historie kann die weitere Erstellung weiterhin blockieren.</p>
    {pathname !== "/berechtigungen/pruefungen" && <p><Link className="link" to="/berechtigungen/pruefungen">Offene Prüfungen</Link></p>}
    <p>Die Liste enthält auch bereits geprüfte Einträge.</p>
    {!page.error && !page.loading && <p>Mahnungen: {page.items.length.toLocaleString("de-DE")} geladen{page.nextCursor ? " · weitere verfügbar" : ""}</p>}
    <button className="btn" type="button" disabled={page.loading} onClick={page.reload}>Quarantäne neu laden</button>
    {page.error && <p role="alert" className="error-text">{page.error}</p>}
    {page.loading && <p role="status">Quarantäne wird geladen…</p>}
    {!page.error && !page.loading && page.items.length === 0 && <p>Keine Mahnungen ohne Abrechnungskontext vorhanden. Dies bestätigt keine vollständige Quellenprüfung.</p>}
    <div className="space-y-4">{page.items.map(row => <ObservationCard key={row.id} row={row} />)}</div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </div>;
}

export function DunningQuarantinePage() {
  const { session } = useAuth();
  return session?.role === "OWNER_ADMIN" ? <Inventory key={session.organization_id} /> : <p className="error-text">Zugriff nur für Inhaber/Admin.</p>;
}
