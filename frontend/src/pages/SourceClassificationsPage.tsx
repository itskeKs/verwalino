import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";

import { sourceClassificationApi } from "../api/endpoints";
import type { SourceClassificationCommand as ReviewCommand, PendingSourceClassification as PendingSource, SourceClassificationReceipt as Receipt } from "../api/types";
const endpoint = sourceClassificationApi.base;

function ReviewReceipt({ receipt }: { receipt: Receipt }) {
  return <section className="card" aria-label="Prüfnachweis">
    <h2>Altquelle eingestuft</h2>
    <p>{receipt.decision === "ADMITTED" ? "Für die spätere Aktivierung freigegeben" : "Für die spätere Aktivierung gesperrt"} · {new Date(receipt.created_at).toLocaleString("de-DE")}</p>
    <p className="whitespace-pre-wrap break-words">{receipt.reason}</p>
    <p><Link to={`/objekte/${receipt.property_id}`}>Zielobjekt anzeigen</Link> · <Link to={`/altquellen/${receipt.id}`}>Prüfnachweis öffnen</Link></p>
    <details><summary>Original identifizieren</summary><p className="break-all">SHA-256: {receipt.content_sha256}</p><p className="break-all">Dokument: {receipt.document_id}</p></details>
  </section>;
}

function ReviewForm({ source }: { source: PendingSource }) {
  const [decision, setDecision] = useState<ReviewCommand["decision"]>(source.can_admit ? "ADMITTED" : "QUARANTINED");
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const command = useRef<{ body: ReviewCommand; key: string } | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !confirmed || !reason.trim() || !source.quiescent || (decision === "ADMITTED" && !source.can_admit)) return;
    command.current ??= { key: crypto.randomUUID(), body: { document_id: source.document_id, property_id: source.property_id,
      source_context_digest: source.source_context_digest, content_sha256: source.content_sha256, decision, reason: reason.trim() } };
    setSubmitted(true); setBusy(true); setError(null);
    try { setReceipt(await sourceClassificationApi.classify(command.current.body, command.current.key)); }
    catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? "Die Quelle ist mit Ihren aktuellen Rechten nicht mehr verfügbar." :
        e instanceof ApiError && e.status === 409 ? "Der Prüfstand hat sich geändert oder die Quelle ist bereits eingestuft. Bitte die Liste neu laden und erneut prüfen." :
          "Keine bestätigte Antwort erhalten. Wiederholen Sie denselben Auftrag; Ihre Angaben bleiben erhalten.");
    } finally { setBusy(false); }
  }
  if (receipt) return <ReviewReceipt receipt={receipt} />;
  return <form className="card min-w-0" onSubmit={submit}>
    <h2 className="break-words">Original {source.document_id.slice(0, 8)}</h2>
    <p>Zielobjekt: <Link to={`/objekte/${source.property_id}`}>{source.property_name}</Link> · {source.linked_import_count} Importverknüpfung(en)</p>
    {source.can_preview ? <p><a href={`${endpoint}/originals/${source.document_id}/content`} target="_blank" rel="noreferrer">Eigenes Original prüfen</a></p> : <p>Die Originaldatei ist für diese Prüfung nicht zugänglich.</p>}
    {!source.can_admit && <p>Die Quelle kann nicht freigegeben werden. Sie können sie für die spätere Aktivierung sperren, ohne ihre Herkunft umzuschreiben.</p>}
    {!source.quiescent && <p role="status">Am verknüpften Import wird noch gearbeitet. Bitte nach Abschluss neu laden.</p>}
    <div className="review-decision"><label htmlFor={`decision-${source.id}`}>Entscheidung für die spätere Aktivierung</label>
    <select id={`decision-${source.id}`} value={decision} disabled={busy || submitted || !source.quiescent} onChange={e => { setDecision(e.target.value as ReviewCommand["decision"]); setConfirmed(false); }}>
      <option value="ADMITTED" disabled={!source.can_admit}>Quelle freigeben</option>
      <option value="QUARANTINED">Quelle sperren</option>
    </select></div>
    <details><summary>Original identifizieren</summary><p className="break-all">SHA-256: {source.content_sha256}</p></details>
    <div className="review-reason"><label htmlFor={`reason-${source.id}`}>Begründung der Einstufung</label>
      <textarea id={`reason-${source.id}`} className="w-full" required maxLength={2000} rows={3} value={reason} disabled={busy || submitted} onChange={e => setReason(e.target.value)} /></div>
    <label className="review-confirmation"><input type="checkbox" checked={confirmed} disabled={busy || submitted} onChange={e => setConfirmed(e.target.checked)} />
      {decision === "ADMITTED" ? "Ich habe mein Original und das Zielobjekt geprüft und bestätige die Freigabe für die spätere Aktivierung." : "Ich bestätige die Sperre dieser Quelle für die spätere Aktivierung. Ihre Herkunft bleibt unverändert."}</label>
    {error && <p role="alert" className="error-text">{error}</p>}
    <button className="btn btn--primary" disabled={busy || !confirmed || !reason.trim() || !source.quiescent} type="submit">{busy ? "Speichere…" : submitted ? "Denselben Auftrag wiederholen" : "Einstufung speichern"}</button>
  </form>;
}

function PendingReviews() {
  const page = usePagedList<PendingSource>(endpoint + "/pending/page");
  const [generation, setGeneration] = useState(0);
  function refresh() { setGeneration(value => value + 1); page.reload(); }
  return <>
    <p>Hier erscheinen bereits zugeordnete Originale in den Datenbereichen, für die Sie ausdrückliche Prüf- und Freigaberechte haben.</p>
    <ListSearch label="Nach Dokument-ID suchen" onSearch={page.setQuery} />
    <button className="btn mb-4" disabled={page.loading} onClick={refresh}>Liste neu laden</button>
    {page.error && <p role="alert" className="error-text">{page.error}</p>}
    {page.loading && <p role="status">Lade Quellen…</p>}
    {!page.loading && !page.error && !page.items.length && <p>Keine offenen Altquellen verfügbar.</p>}
    <div className="space-y-4">{page.items.map(source => <ReviewForm key={`${generation}:${source.id}`} source={source} />)}</div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </>;
}

function ReceiptDetail({ reviewId }: { reviewId: string }) {
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    sourceClassificationApi.get(reviewId, controller.signal).then(value => { if (!controller.signal.aborted) setReceipt(value); })
      .catch(() => { if (!controller.signal.aborted) setError("Prüfnachweis mit Ihren aktuellen Rechten nicht verfügbar."); });
    return () => controller.abort();
  }, [reviewId]);
  return <>{error ? <p role="alert" className="error-text">{error}</p> : receipt ? <ReviewReceipt receipt={receipt} /> : <p role="status">Lade Prüfnachweis…</p>}</>;
}

export function SourceClassificationsPage() {
  const { reviewId } = useParams();
  const { session } = useAuth();
  const canWrite = session?.role === "OWNER_ADMIN";
  return <div className="paged-list source-admission-review min-w-0"><h1>Altquellen prüfen</h1>
    <p>Diese Einstufung bereitet die spätere Zugriffskontrolle vor. Sie ist noch nicht aktiviert; auch eine hier dokumentierte Sperre schränkt den bisherigen Zugriff noch nicht ein. Unklare Herkunft wird nicht nachträglich ergänzt.</p>
    {reviewId ? <><Link to="/altquellen">Zur Liste der Altquellen</Link><ReceiptDetail key={reviewId} reviewId={reviewId} /></> :
      canWrite ? <PendingReviews /> : <p>Die Einstufung erfordert Administratorrechte und ausdrückliche Rechte am Datenbereich.</p>}
  </div>;
}
