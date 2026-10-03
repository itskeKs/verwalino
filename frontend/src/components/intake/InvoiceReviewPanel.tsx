import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import type { AssignmentProposal, ExtractionRevision } from '../../api/intake';
import { invoiceReviewsApi, type InvoiceReviewCommand, type InvoiceReviewHistory } from '../../api/invoiceReviews';

type Form = { seller_name: string; document_number: string; document_kind: '' | 'invoice' | 'credit_note'; currency: '' | 'EUR';
  invoice_date: string; service_period_from: string; service_period_to: string; gross_amount: string; vat_amount: string; vat_rate: string };
type Draft = { expected: string | null; proposal: string; extraction: string; form: Form; reason: string; acknowledged: boolean; origin: string };
const fields: { key: keyof Omit<Form, 'document_kind' | 'currency'>; label: string; type?: string; max?: number }[] = [
  { key: 'seller_name', label: 'Aussteller geprüft', max: 255 }, { key: 'document_number', label: 'Belegnummer geprüft', max: 100 },
  { key: 'invoice_date', label: 'Rechnungsdatum geprüft', type: 'date' }, { key: 'service_period_from', label: 'Leistungszeitraum von', type: 'date' },
  { key: 'service_period_to', label: 'Leistungszeitraum bis', type: 'date' }, { key: 'gross_amount', label: 'Bruttobetrag geprüft' },
  { key: 'vat_amount', label: 'Umsatzsteuerbetrag (optional)' }, { key: 'vat_rate', label: 'Umsatzsteuersatz (optional)' },
];
const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60';
function fresh(history: InvoiceReviewHistory, proposal: AssignmentProposal, extraction: ExtractionRevision): Draft {
  const prior = history.items[0]; const reuse = prior?.assignment_proposal_id === proposal.id;
  const value = (key: string) => typeof extraction.payload?.[key] === 'string' ? extraction.payload[key] as string : '';
  return { expected: prior?.id ?? null, proposal: proposal.id, extraction: extraction.id, reason: '', acknowledged: false,
    origin: reuse ? 'Startwerte aus dem letzten menschlichen Prüfstand.' : 'Startwerte aus ungeprüfter Extraktion. Fehlende Werte bitte am Original prüfen.',
    form: reuse ? { seller_name: prior.seller_name, document_number: prior.document_number, document_kind: prior.document_kind, currency: prior.currency,
      invoice_date: prior.invoice_date, service_period_from: prior.service_period_from, service_period_to: prior.service_period_to,
      gross_amount: prior.gross_amount, vat_amount: prior.vat_amount ?? '', vat_rate: prior.vat_rate ?? '' }
      : { seller_name: value('vendor_name'), document_number: value('document_number'), document_kind: '', currency: '', invoice_date: value('invoice_date'),
        service_period_from: value('service_period_from'), service_period_to: value('service_period_to'), gross_amount: value('gross_amount'), vat_amount: value('vat_amount'), vat_rate: value('vat_rate') } };
}
function reviewed(draft: Draft): InvoiceReviewCommand | null {
  const f = draft.form; const amount = f.gross_amount.trim().replace(',', '.'); const vat = f.vat_amount.trim().replace(',', '.') || null; const rate = f.vat_rate.trim().replace(',', '.') || null;
  const money = /^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$/;
  if (!f.seller_name.trim() || !f.document_number.trim() || !f.document_kind || f.currency !== 'EUR' || !draft.acknowledged || !draft.reason.trim()
    || ![f.invoice_date, f.service_period_from, f.service_period_to].every(value => /^\d{4}-\d{2}-\d{2}$/.test(value)) || f.service_period_from > f.service_period_to
    || !money.test(amount) || /^-?0(?:\.0{1,2})?$/.test(amount) || (f.document_kind === 'credit_note') !== amount.startsWith('-')
    || (vat !== null && !money.test(vat)) || (rate !== null && !/^(?:(?:0|[1-9][0-9]?)(?:\.[0-9]{1,2})?|100(?:\.0{1,2})?)$/.test(rate))) return null;
  return { ...f, seller_name: f.seller_name.trim(), document_number: f.document_number.trim(), document_kind: f.document_kind, currency: f.currency,
    gross_amount: amount, vat_amount: vat, vat_rate: rate, assignment_proposal_id: draft.proposal, fields_reviewed: true, reason: draft.reason.trim() };
}

export function InvoiceReviewPanel({ logicalId, proposal, extraction, ready, parentBusy, parentGeneration, onDenied }: {
  logicalId: string; proposal: AssignmentProposal | null; extraction: ExtractionRevision | null; ready: boolean; parentBusy: boolean; parentGeneration: number; onDenied: () => void;
}) {
  const [history, setHistory] = useState<InvoiceReviewHistory | null>(null); const [draft, setDraft] = useState<Draft | null>(null);
  const [generation, setGeneration] = useState(0); const [busy, setBusy] = useState(true); const [sending, setSending] = useState(false);
  const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ expected: string | null; key: string; body: InvoiceReviewCommand } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true);
    invoiceReviewsApi.history(logicalId, controller.signal).then(value => { if (!controller.signal.aborted) {
      setHistory(value); if (proposal && extraction && ready) setDraft(previous => previous ?? fresh(value, proposal, extraction));
    } }).catch(failure => { if (!controller.signal.aborted) {
      setHistory(null); setError('Feldprüfungen derzeit nicht verfügbar. Bitte erneut laden.');
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { pending.current = null; setSubmitted(false); setDraft(null); onDenied(); }
    } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [logicalId, proposal, extraction, ready, generation, parentGeneration, onDenied]);
  const blocked = busy || parentBusy || sending;
  const stale = conflict || !!draft && (draft.proposal !== proposal?.id || draft.extraction !== extraction?.id || draft.expected !== (history?.items[0]?.id ?? null));
  const canReview = !!proposal && history?.reviewable_assignment_id === proposal.id;
  function edit(key: keyof Form, value: string) { if (draft) setDraft({ ...draft, acknowledged: false, form: { ...draft.form, [key]: value } }); }
  async function save() {
    if (blocked || !history || !canReview || !draft || (!pending.current && (stale || !ready))) return;
    if (!pending.current) {
      const body = reviewed(draft); if (!body) { setError('Bitte Belegart, Währung, Pflichtfelder und Zeitraum prüfen. Rechnungen benötigen positive, Gutschriften negative Beträge mit höchstens zwei Nachkommastellen.'); return; }
      pending.current = { expected: draft.expected, key: crypto.randomUUID(), body };
    }
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      await invoiceReviewsApi.create(logicalId, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setConflict(false); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if ([401, 403, 404].includes(failure.status)) { setHistory(null); setDraft(null); }
        if (failure.status === 409) setConflict(true);
        setError(failure.status === 409 ? 'Der Prüfstand oder seine Grundlage wurde geändert. Ihre Eingaben bleiben erhalten.' : 'Feldprüfung nicht gespeichert. Bitte Werte und Berechtigung prüfen.');
        setGeneration(value => value + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Feldprüfung erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="card min-w-0 space-y-4" aria-label="Rechnungsfelder prüfen">
    <div className="flex flex-wrap justify-between gap-3"><h2>Rechnungsfelder prüfen</h2><button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Feldprüfungen aktualisieren</button></div>
    <p>Hier halten Sie Ihre Prüfung der Feldwerte fest. Zuordnungskonflikte, Steuerbehandlung und Dubletten bleiben separat zu klären; es werden keine Kosten bestätigt.</p>
    {busy && <p role="status">Lade Feldprüfungen…</p>}{error && <p role="alert" className="error-text">{error}</p>}
    {history && <>
      {!history.items.length && <p>Noch keine lesbare Feldprüfung.</p>}
      {history.items.map((review, index) => <details key={review.id} open={index === 0} className="min-w-0 space-y-2">
        <summary className="cursor-pointer">Menschlicher Prüfstand · Version {review.revision_number} · {new Date(review.created_at).toLocaleString('de-DE')}</summary>
        <p className="break-words">{review.seller_name} · {review.document_number} · {review.document_kind === 'credit_note' ? 'Gutschrift' : 'Rechnung'}</p>
        <p>Brutto: {review.gross_amount} {review.currency} · Umsatzsteuer: {review.vat_amount ?? 'unbekannt'} · Satz: {review.vat_rate === null ? 'unbekannt' : review.vat_rate + ' %'}</p>
        <p>Rechnungsdatum: {review.invoice_date} · Leistungszeitraum: {review.service_period_from} bis {review.service_period_to}</p>
        <p className="break-words">{review.reason}</p>
        {review.assignment_state === 'blocked' && <p>Der zugrunde liegende Zuordnungsvorschlag bleibt gesperrt.</p>}
        {review.assignment_proposal_id !== proposal?.id && <p>Prüfstand einer früheren Zuordnung; nicht automatisch übernommen.</p>}
      </details>)}
      {history.next_before_revision && <p>Hier werden die letzten 20 lesbaren Feldprüfungen angezeigt.</p>}
      {!ready && <p>Eine aktuelle Zuordnung auf geprüfter Aufteilung und verfügbarem Extraktionsstand wird benötigt.</p>}
      {!canReview && <p>Die Feldprüfung ist derzeit nur lesbar.</p>}
      {canReview && draft && <>
        {stale && !submitted && <div className="space-y-2"><p>Prüfen Sie die geänderte Grundlage. Beim Übernehmen werden die Eingaben durch deren Startwerte ersetzt.</p>
          <button className="btn" disabled={blocked || !ready || !proposal || !extraction} onClick={() => { if (proposal && extraction) { setDraft(fresh(history, proposal, extraction)); setConflict(false); setError(null); } }}>Neue Feldgrundlage übernehmen</button></div>}
        <p>{draft.origin}</p><p>Beträge ohne Tausendertrennzeichen eingeben. Bei Gutschriften das Minuszeichen ausdrücklich übernehmen. Leere Umsatzsteuerfelder bedeuten unbekannt.</p>
        <fieldset disabled={blocked || submitted || !ready} className="min-w-0 space-y-3">
          <div className="grid min-w-0 gap-3 md:grid-cols-2">
            <div><label htmlFor="review-kind">Belegart geprüft</label><select id="review-kind" className={inputClass} value={draft.form.document_kind} onChange={event => edit('document_kind', event.target.value)}><option value="">Bitte wählen</option><option value="invoice">Rechnung</option><option value="credit_note">Gutschrift</option></select></div>
            <div><label htmlFor="review-currency">Währung geprüft</label><select id="review-currency" className={inputClass} value={draft.form.currency} onChange={event => edit('currency', event.target.value)}><option value="">Bitte wählen</option><option value="EUR">EUR</option></select></div>
            {fields.map(field => <div key={field.key} className="min-w-0"><label htmlFor={`review-${field.key}`}>{field.label}</label>
              <input id={`review-${field.key}`} className={inputClass} type={field.type ?? 'text'} maxLength={field.max ?? 32} value={draft.form[field.key]} onChange={event => edit(field.key, event.target.value)} /></div>)}
          </div>
          <label className="block" htmlFor="review-reason">Begründung der Feldprüfung</label><textarea id="review-reason" className={inputClass} maxLength={1000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })} />
          <label className="flex items-start gap-2"><input type="checkbox" checked={draft.acknowledged} onChange={event => setDraft({ ...draft, acknowledged: event.target.checked })} />Die angegebenen Rechnungsfelder am Original geprüft; fehlende Werte bewusst ergänzt.</label>
        </fieldset>
        <button className="btn" disabled={blocked || (!submitted && (stale || !ready || !draft.acknowledged || !draft.reason.trim()))} onClick={() => void save()}>{submitted ? 'Dieselbe Feldprüfung erneut senden' : 'Feldprüfung speichern'}</button>
      </>}
    </>}
  </section>;
}
