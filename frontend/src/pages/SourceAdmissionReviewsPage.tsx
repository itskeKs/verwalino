import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { usePagedList } from "../hooks/usePagedList";
import { ListSearch, MoreResults } from "../components/ListSearch";

import { sourceAdmissionReviewApi } from "../api/endpoints";
import type { SourceAdmissionReviewCommand as ReviewCommand, PendingSourceAdmissionReview as PendingSource, SourceAdmissionReviewReceipt as Receipt } from "../api/types";
const endpoint = sourceAdmissionReviewApi.base;

function ReviewReceipt({ receipt }: { receipt: Receipt }) {
  return <section className="card" aria-label="Freigabenachweis">
    <h2>Quellenfreigabe dokumentiert</h2>
    <p>Freigegeben am {new Date(receipt.created_at).toLocaleString("de-DE")} · Revision {receipt.draft_revision}</p>
    <p className="whitespace-pre-wrap break-words">{receipt.reason}</p>
    <p><Link to={`/objekte/${receipt.property_id}`}>Zielobjekt anzeigen</Link> · <Link to={`/quellenfreigaben/${receipt.id}`}>Freigabenachweis öffnen</Link></p>
    <details><summary>Original identifizieren</summary><p className="break-all">SHA-256: {receipt.content_sha256}</p><p className="break-all">Dokument: {receipt.document_id}</p></details>
  </section>;
}

function ReviewForm({ source }: { source: PendingSource }) {
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const command = useRef<{ body: ReviewCommand; key: string } | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || !confirmed || !reason.trim()) return;
    command.current ??= { key: crypto.randomUUID(), body: { document_id: source.document_id, import_id: source.import_id,
      draft_revision: source.draft_revision, content_sha256: source.content_sha256, reason: reason.trim() } };
    setSubmitted(true); setBusy(true); setError(null);
    try { setReceipt(await sourceAdmissionReviewApi.review(command.current.body, command.current.key)); }
    catch (e) {
      setError(e instanceof ApiError && e.status === 404 ? "Die Quelle ist mit Ihren aktuellen Rechten nicht mehr verfügbar." :
        e instanceof ApiError && e.status === 409 ? "Der Prüfstand hat sich geändert oder die Quelle ist bereits freigegeben. Bitte die Liste neu laden und erneut prüfen." :
          "Keine bestätigte Antwort erhalten. Wiederholen Sie denselben Auftrag; Ihre Angaben bleiben erhalten.");
    } finally { setBusy(false); }
  }
  if (receipt) return <ReviewReceipt receipt={receipt} />;
  return <form className="card min-w-0" onSubmit={submit}>
    <h2 className="break-words">{source.title}</h2>
    <p>Zielobjekt: <Link to={`/objekte/${source.property_id}`}>{source.property_name}</Link> · Revision {source.draft_revision}</p>
    {source.can_preview ? <p><a className="break-words" href={`${endpoint}/originals/${source.document_id}/content`} target="_blank" rel="noreferrer">Original prüfen: {source.original_filename}</a></p> : <p>Die Originaldatei ist mit Ihren aktuellen Rechten nicht zugänglich.</p>}
    <details><summary>Original identifizieren</summary><p className="break-all">SHA-256: {source.content_sha256}</p></details>
    <div className="review-reason"><label htmlFor={`reason-${source.id}`}>Begründung der Quellenfreigabe</label>
      <textarea id={`reason-${source.id}`} className="w-full" required maxLength={2000} rows={3} value={reason} disabled={busy || submitted} onChange={e => setReason(e.target.value)} /></div>
    <label className="review-confirmation"><input type="checkbox" checked={confirmed} disabled={busy || submitted} onChange={e => setConfirmed(e.target.checked)} />
      Ich habe Original und Zielobjekt geprüft und gebe diese Quelle für den vorbereiteten Datenbereich des Objekts frei.</label>
    {error && <p role="alert" className="error-text">{error}</p>}
    <button className="btn btn--primary" disabled={busy || !confirmed || !reason.trim()} type="submit">{busy ? "Speichere…" : submitted ? "Denselben Auftrag wiederholen" : "Quelle freigeben"}</button>
  </form>;
}

function PendingReviews() {
  const page = usePagedList<PendingSource>(endpoint + "/pending/page");
  const [generation, setGeneration] = useState(0);
  function refresh() { setGeneration(value => value + 1); page.reload(); }
  return <>
    <p>Hier erscheinen eigene Originale, die Sie mit Ihren aktuellen Rechten für das Zielobjekt prüfen und freigeben können.</p>
    <ListSearch label="Originale suchen" onSearch={page.setQuery} />
    <button className="btn mb-4" disabled={page.loading} onClick={refresh}>Liste neu laden</button>
    {page.error && <p role="alert" className="error-text">{page.error}</p>}
    {page.loading && <p role="status">Lade Quellen…</p>}
    {!page.loading && !page.error && !page.items.length && <p>Keine offenen Quellenfreigaben verfügbar.</p>}
    <div className="space-y-4">{page.items.map(source => <ReviewForm key={`${generation}:${source.id}`} source={source} />)}</div>
    <MoreResults available={!!page.nextCursor} loading={page.loading} onMore={() => void page.loadMore()} />
  </>;
}

function ReceiptDetail({ reviewId }: { reviewId: string }) {
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    sourceAdmissionReviewApi.get(reviewId, controller.signal).then(value => { if (!controller.signal.aborted) setReceipt(value); })
      .catch(() => { if (!controller.signal.aborted) setError("Freigabenachweis mit Ihren aktuellen Rechten nicht verfügbar."); });
    return () => controller.abort();
  }, [reviewId]);
  return <>{error ? <p role="alert" className="error-text">{error}</p> : receipt ? <ReviewReceipt receipt={receipt} /> : <p role="status">Lade Freigabenachweis…</p>}</>;
}

export function SourceAdmissionReviewsPage() {
  const { reviewId } = useParams();
  const { session } = useAuth();
  const canWrite = session?.role === "OWNER_ADMIN" || session?.role === "CLERK";
  return <div className="paged-list source-admission-review min-w-0"><h1>Quellenfreigaben</h1>
    <p>Die Freigabe dokumentiert Ihre heutige Entscheidung für den vorbereiteten Datenbereich. Die gemeinsame Zugriffskontrolle ist noch nicht aktiviert; bestehender Zugriff wird dadurch noch nicht eingeschränkt.</p>
    {reviewId ? <><Link to="/quellenfreigaben">Zur Liste der Quellenfreigaben</Link><ReceiptDetail key={reviewId} reviewId={reviewId} /></> :
      canWrite ? <PendingReviews /> : <p>Quellenfreigaben erfordern Schreibrechte.</p>}
  </div>;
}
