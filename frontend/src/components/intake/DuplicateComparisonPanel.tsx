import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import type { InvoiceReview } from '../../api/invoiceReviews';
import { duplicateComparisonsApi, type DuplicateCandidate, type DuplicateComparison, type DuplicateCommand, type DuplicateHistory, type DuplicatePage, type DuplicateVerdict } from '../../api/duplicateComparisons';

type Draft = { subject: string; candidate: DuplicateCandidate; expected: string | null; verdict: '' | DuplicateVerdict; reason: string; acknowledged: boolean };
type Pair = { subject: InvoiceReview; reference: InvoiceReview; history: DuplicateHistory };
const verdicts = { same_document: 'Derselbe Beleg', distinct_documents: 'Verschiedene Belege', replacement_requested: 'Ersetzung angefragt' };
const labels: Record<string, string> = { seller_name: 'Aussteller', document_number: 'Belegnummer', document_kind: 'Belegart', currency: 'Währung', invoice_date: 'Rechnungsdatum', service_period_from: 'Leistungsbeginn', service_period_to: 'Leistungsende', gross_amount: 'Brutto', vat_amount: 'Umsatzsteuerbetrag', vat_rate: 'Umsatzsteuersatz' };
function verdictLabel(comparison: DuplicateComparison, referenceLogical: string) {
  if (comparison.verdict !== 'replacement_requested') return verdicts[comparison.verdict];
  return comparison.reference_logical_id === referenceLogical ? 'Ausgangsbeleg soll Vergleichsbeleg ersetzen' : 'Vergleichsbeleg soll Ausgangsbeleg ersetzen';
}
const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60';
const denied = (failure: unknown) => failure instanceof ApiError && [401, 403, 404].includes(failure.status);
function Review({ review, title }: { review: InvoiceReview; title: string }) {
  return <article className="min-w-0 space-y-2 rounded-md border border-input p-3"><h3>{title} · Prüfstand {review.revision_number}</h3>
    <p className="break-words">{review.seller_name} · {review.document_number}</p><p>{review.document_kind === 'credit_note' ? 'Gutschrift' : 'Rechnung'} · {review.gross_amount} {review.currency}</p>
    <p>Rechnungsdatum: {review.invoice_date}<br />Leistung: {review.service_period_from} bis {review.service_period_to}</p>
    <p>Umsatzsteuer: {review.vat_amount ?? 'unbekannt'} · Satz: {review.vat_rate ?? 'unbekannt'}</p>
    <a className="link" target="_blank" rel="noreferrer" href={`/posteingang/belege/${review.document_ref_id}?logical_document_id=${encodeURIComponent(review.logical_document_id)}&extraction_revision_id=${encodeURIComponent(review.extraction_revision_id)}`}>{title} im neuen Tab öffnen</a>
  </article>;
}
export function DuplicateComparisonPanel({ reviewId, ready, parentBusy, parentGeneration }: {
  reviewId: string | null; ready: boolean; parentBusy: boolean; parentGeneration: number;
}) {
  const [page, setPage] = useState<DuplicatePage | null>(null); const [cursor, setCursor] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null); const [pair, setPair] = useState<Pair | null>(null);
  const [generation, setGeneration] = useState(0); const [busy, setBusy] = useState(false); const [sending, setSending] = useState(false);
  const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ subject: string; expected: string | null; key: string; body: DuplicateCommand } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const draftSubject = draft?.subject ?? null; const draftReference = draft?.candidate.review_id ?? null;
  useEffect(() => { setCursor(null); }, [reviewId]);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setPage(null); setPair(null);
    if (!reviewId) { setBusy(false); return () => controller.abort(); }
    // Revalidate both exact retained reviews independently of the current candidate page.
    const pairRequest = draftSubject && draftReference ? Promise.all([
      duplicateComparisonsApi.review(draftSubject, controller.signal), duplicateComparisonsApi.review(draftReference, controller.signal),
      duplicateComparisonsApi.history(draftSubject, draftReference, controller.signal),
    ]).then(([subject, reference, history]) => ({ subject, reference, history })) : Promise.resolve(null);
    Promise.allSettled([duplicateComparisonsApi.candidates(reviewId, cursor, controller.signal), pairRequest]).then(([candidates, retained]) => {
      if (controller.signal.aborted) return;
      if (retained.status === 'rejected' && denied(retained.reason)) { pending.current = null; setSubmitted(false); setDraft(null); setError('Vergleichsquellen nicht mehr lesbar. Bitte Berechtigung und Prüfstand prüfen.'); return; }
      if (candidates.status === 'fulfilled') setPage(candidates.value);
      else setError('Kandidaten derzeit nicht verfügbar oder Prüfgrundlage geändert. Feldprüfungen und Vergleich neu laden.');
      if (retained.status === 'fulfilled') setPair(retained.value);
      else setError('Die beiden Vergleichsquellen konnten nicht erneut geprüft werden. Bitte neu laden.');
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [reviewId, cursor, draftSubject, draftReference, generation, parentGeneration]);
  const blocked = busy || parentBusy || sending;
  const authorized = !!reviewId && !!draft && pair?.subject.id === draft.subject && pair.reference.id === draft.candidate.review_id;
  const current = page?.items.find(item => item.logical_document_id === draft?.candidate.logical_document_id);
  const stale = conflict || !!draft && (reviewId !== draft.subject || current?.review_id !== draft.candidate.review_id || draft.expected !== (pair?.history.items[0]?.id ?? null));
  const canCompare = ready && current?.can_compare && !current.evidence.kind_conflict;
  function select(candidate: DuplicateCandidate) {
    if (!reviewId) return;
    setDraft({ subject: reviewId, candidate, expected: candidate.latest_comparison?.id ?? null, verdict: '', reason: '', acknowledged: false });
    setConflict(false); setError(null);
  }
  async function save() {
    if (blocked || !draft || !authorized || (!pending.current && (!canCompare || stale || !draft.verdict || !draft.reason.trim() || !draft.acknowledged))) return;
    if (!pending.current) pending.current = { subject: draft.subject, expected: draft.expected, key: crypto.randomUUID(),
      body: { reference_review_id: draft.candidate.review_id, verdict: draft.verdict as DuplicateVerdict, reason: draft.reason.trim(), comparison_reviewed: true } };
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      await duplicateComparisonsApi.create(request.subject, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setPair(null); setConflict(false); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if (denied(failure)) { setPage(null); setPair(null); setDraft(null); }
        if (failure.status === 409) setConflict(true);
        setError(failure.status === 409 ? 'Vergleich oder Prüfgrundlage geändert. Ihre Eingaben bleiben erhalten.' : 'Vergleich nicht gespeichert. Bitte Prüfstände, Auswahl und Berechtigung prüfen.');
        setGeneration(value => value + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie denselben Vergleich erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="card min-w-0 space-y-4" aria-label="Dubletten vergleichen">
    <div className="flex flex-wrap justify-between gap-3"><h2>Dubletten vergleichen</h2><button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Vergleiche aktualisieren</button></div>
    <p>Vergleichen Sie mögliche Dubletten mit dem Original. Angezeigt werden nur für Sie lesbare Prüfstände im selben Abrechnungskreis. Eine leere Liste bestätigt keine Dublettenfreiheit.</p>
    <p>Ein Vergleich dokumentiert Ihre Prüfung. Er bucht keine Kosten, löscht keine Belege und erteilt keine Kostenfreigabe.</p>
    {busy && <p role="status">Lade Vergleichsquellen…</p>}{error && <p role="alert" className="error-text">{error}</p>}
    {!reviewId && <p>Für die Suche wird ein menschlicher Feldprüfstand benötigt.</p>}
    {page && <>
      {!page.items.length && <p>Keine lesbaren Kandidaten in dieser Suche.</p>}
      <ul className="space-y-3">{page.items.map(item => <li key={item.review_id} className="min-w-0 space-y-2 rounded-md border border-input p-3">
        <p className="break-words">{item.seller_name} · {item.document_number} · {item.gross_amount} {item.currency}</p>
        <p>{item.evidence.match_reasons.includes('same_bytes_and_range') ? 'Identische Originaldatei und derselbe Seitenbereich.' : 'Übereinstimmende geprüfte Rechnungsmerkmale; möglicher Treffer.'}</p>
        {item.evidence.kind_conflict && <p>Rechnung und Gutschrift widersprechen sich bei identischem Original. Zuerst die Feldprüfung korrigieren.</p>}
        {item.latest_comparison && <p>Letzter Paarvergleich: {verdictLabel(item.latest_comparison, item.logical_document_id)} · Version {item.latest_comparison.revision_number} · {item.latest_comparison.subject_review_id === reviewId && item.latest_comparison.reference_review_id === item.review_id || item.latest_comparison.reference_review_id === reviewId && item.latest_comparison.subject_review_id === item.review_id ? 'gleiche Prüfstände' : 'frühere Prüfstände; erneut prüfen'}</p>}
        <button className="btn" disabled={blocked || !!draft} onClick={() => select(item)}>Belege gegenüberstellen · {item.document_number}</button>
      </li>)}</ul>
      <div className="flex flex-wrap gap-2">{cursor && <button className="btn" disabled={blocked || !!draft} onClick={() => setCursor(null)}>Erste Kandidatenseite</button>}
        {page.next_cursor && <button className="btn" disabled={blocked || !!draft} onClick={() => setCursor(page.next_cursor)}>Weitere Kandidaten</button>}</div>
    </>}
    {draft && authorized && pair && <div className="space-y-4 min-w-0">
      <div className="grid min-w-0 gap-3 lg:grid-cols-2"><Review review={pair.subject} title="Ausgangsbeleg" /><Review review={pair.reference} title="Vergleichsbeleg" /></div>
      {draft.candidate.evidence.field_disagreements.length > 0 && <p>Abweichende Felder: {draft.candidate.evidence.field_disagreements.map(key => labels[key] ?? key).join(', ')}. Abweichungen werden durch diesen Vergleich nicht korrigiert.</p>}
      {pair.history.items.map(item => <details key={item.id}><summary>Paarvergleich · Version {item.revision_number} · {verdictLabel(item, pair.reference.logical_document_id)}</summary><p className="break-words">{item.reason}</p><p>{new Date(item.created_at).toLocaleString('de-DE')} · Historischer Prüfvermerk, keine Kostenfreigabe</p></details>)}
      {pair.history.next_before_revision && <p>Die letzten 20 lesbaren Paarvergleiche werden angezeigt.</p>}
      {stale && !submitted && <p>Die Vergleichsgrundlage wurde geändert. Schließen Sie diesen Entwurf und wählen Sie die aktuellen Prüfstände neu aus.</p>}
      {!canCompare && !submitted && <p>Dieser Vergleich kann derzeit nur gelesen werden.</p>}
      <fieldset disabled={blocked || submitted || !canCompare || stale} className="space-y-3">
        <label className="block" htmlFor="duplicate-verdict">Ergebnis des Belegvergleichs</label><select id="duplicate-verdict" className={inputClass} value={draft.verdict} onChange={event => setDraft({ ...draft, verdict: event.target.value as Draft['verdict'], acknowledged: false })}>
          <option value="">Bitte wählen</option><option value="same_document">Derselbe Beleg</option>{!draft.candidate.evidence.match_reasons.includes('same_bytes_and_range') && <><option value="distinct_documents">Verschiedene Belege</option><option value="replacement_requested">Ausgangsbeleg soll Vergleichsbeleg ersetzen</option></>}</select>
        <p>Eine angefragte Ersetzung benötigt später eine gesonderte Korrekturverknüpfung.</p>
        <label className="block" htmlFor="duplicate-reason">Begründung des Belegvergleichs</label><textarea id="duplicate-reason" className={inputClass} maxLength={2000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value, acknowledged: false })} />
        <label className="flex items-start gap-2"><input type="checkbox" checked={draft.acknowledged} onChange={event => setDraft({ ...draft, acknowledged: event.target.checked })} />Beide Originale und die dargestellten Prüfstände verglichen.</label>
      </fieldset>
      <div className="flex flex-wrap gap-2"><button className="btn" disabled={blocked || (!submitted && (!canCompare || stale || !draft.verdict || !draft.reason.trim() || !draft.acknowledged))} onClick={() => void save()}>{submitted ? 'Denselben Vergleich erneut senden' : 'Belegvergleich speichern'}</button>
        {!submitted && <button className="btn" disabled={blocked} onClick={() => { setDraft(null); setPair(null); setConflict(false); setError(null); }}>Vergleichsentwurf schließen</button>}</div>
    </div>}
  </section>;
}
