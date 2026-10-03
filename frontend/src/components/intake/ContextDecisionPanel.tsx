import { useEffect, useRef, useState } from 'react';
import { AllocationProposalPanel } from './AllocationProposalPanel';
import { ApiError } from '../../api/client';
import { contextDecisionsApi, type ContextCommand, type ContextHistory, type ContextPreview, type ContextSnapshot } from '../../api/contextDecisions';

type Draft = { preview: ContextPreview; review: string; expected: string | null; quotation: string; locator: string; note: string; acknowledged: boolean };
const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60';
function Snapshot({ value }: { value: ContextSnapshot }) {
  return <div className="min-w-0 space-y-1 break-words"><p>{value.property.name} · {value.property.street} · {value.property.zip_code} {value.property.city}</p>
    <p>{value.scope.label} · {value.scope.scope_type} · {value.scope.whole_property ? 'Gesamtes Objekt' : 'Teil des Objekts'}</p>
    <p>Gültig von {value.scope.valid_from} bis {value.scope.valid_to ?? 'unbefristet'}</p></div>;
}
function fresh(preview: ContextPreview, history: ContextHistory): Draft {
  return { preview, review: preview.invoice_review_id!, expected: history.items[0]?.id ?? null, quotation: '', locator: '', note: '', acknowledged: false };
}
export function ContextDecisionPanel({ logicalId, proposalId, reviewId, ready, parentBusy, parentGeneration, onDenied }: {
  logicalId: string; proposalId: string | null; reviewId: string | null; ready: boolean; parentBusy: boolean; parentGeneration: number; onDenied: () => void;
}) {
  const [view, setView] = useState<{ history: ContextHistory; preview: ContextPreview | null; authorizedDraftProposal: string | null } | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null); const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState(true); const [sending, setSending] = useState(false); const [submitted, setSubmitted] = useState(false);
  const [conflict, setConflict] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ expected: string | null; body: ContextCommand; key: string } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const draftProposal = draft?.preview.assignment_proposal_id ?? null;
  useEffect(() => {
    const controller = new AbortController(); setBusy(true);
    Promise.all([contextDecisionsApi.history(logicalId, controller.signal).catch(failure => {
      if (!controller.signal.aborted && failure instanceof ApiError && [401, 403, 404].includes(failure.status)) onDenied();
      throw failure;
    }), proposalId && reviewId ? contextDecisionsApi.preview(proposalId, controller.signal) : Promise.resolve(null),
      proposalId && reviewId && draftProposal && draftProposal !== proposalId ? contextDecisionsApi.preview(draftProposal, controller.signal) : Promise.resolve(null)])
      .then(([history, preview, retainedPreview]) => { if (!controller.signal.aborted) {
        setView({ history, preview, authorizedDraftProposal: retainedPreview?.assignment_proposal_id ?? preview?.assignment_proposal_id ?? null });
        if (preview?.invoice_review_id && preview.invoice_review_id === reviewId && ready) setDraft(prior => prior ?? fresh(preview, history));
      } }).catch(failure => { if (!controller.signal.aborted) {
        setView(null); setError('Kontextprüfungen derzeit nicht verfügbar. Bitte erneut laden.');
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
          pending.current = null; setSubmitted(false); setDraft(null);
        }
      } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [logicalId, proposalId, reviewId, draftProposal, ready, generation, parentGeneration, onDenied]);
  const blocked = busy || parentBusy || sending;
  const preview = view?.preview;
  const canReview = !!preview?.can_review && ready && !!reviewId && preview.invoice_review_id === reviewId
    && preview.snapshot.scope.whole_property && ['WEG', 'MIET'].includes(preview.snapshot.scope.scope_type);
  const draftAuthorized = !!draft && view?.authorizedDraftProposal === draft.preview.assignment_proposal_id;
  const stale = conflict || !!draft && (draft.preview.assignment_proposal_id !== proposalId || draft.review !== reviewId
    || draft.preview.snapshot_digest !== preview?.snapshot_digest || draft.preview.case_event_id !== preview?.case_event_id
    || draft.expected !== (view?.history.items[0]?.id ?? null));
  function edit(key: 'quotation' | 'locator' | 'note', value: string) { if (draft) setDraft({ ...draft, [key]: value, acknowledged: false }); }
  async function save() {
    if (blocked || !draft || !view || !draftAuthorized || (!pending.current && (!canReview || stale))) return;
    if (!pending.current) {
      const page = draft.preview.page_start === null ? null : Number(draft.locator);
      if (!draft.acknowledged || !draft.quotation.trim() || !draft.note.trim() || !draft.locator.trim()
        || (page !== null && (!/^\d+$/.test(draft.locator) || page < draft.preview.page_start! || page > draft.preview.page_end!))) {
        setError('Bitte Fundstelle innerhalb des ausgewählten Belegs, Zitat, Vergleich und Bestätigung ergänzen.'); return;
      }
      pending.current = { expected: draft.expected, key: crypto.randomUUID(), body: { assignment_proposal_id: draft.preview.assignment_proposal_id,
        invoice_review_id: draft.review, snapshot_digest: draft.preview.snapshot_digest, expected_case_event_id: draft.preview.case_event_id,
        quotation: draft.quotation.trim(), page_number: page, xml_path: page === null ? draft.locator.trim() : null,
        comparison_note: draft.note.trim(), context_reviewed: true } };
    }
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      await contextDecisionsApi.create(logicalId, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setConflict(false); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if ([401, 403, 404].includes(failure.status)) { setView(null); setDraft(null); }
        if (failure.status === 409) setConflict(true);
        setError(failure.status === 409 ? 'Die Kontextgrundlage wurde geändert oder wird gerade bearbeitet. Ihre Eingaben bleiben erhalten.' : 'Kontextprüfung nicht gespeichert. Bitte Angaben und Berechtigung prüfen.');
        setGeneration(value => value + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Kontextprüfung erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  const latestDecision = view?.history.items[0];
  return <><section className="card min-w-0 space-y-4" aria-label="Objekt und Abrechnungskreis abgleichen">
    <div className="flex flex-wrap justify-between gap-3"><h2>Objekt und Abrechnungskreis abgleichen</h2><button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Kontextprüfungen aktualisieren</button></div>
    <p>Vergleichen Sie die Objektangaben im Original mit dem vorgesehenen Abrechnungskreis. Die Anschrift des Ausstellers allein belegt das Objekt nicht.</p>
    <p>Diese menschliche Prüfung gilt für genau den ausgewählten Zuordnungsvorschlag und Feldprüfstand. Andere Vorschläge, Steuern und Dubletten bleiben separat zu klären.</p>
    {busy && <p role="status">Lade Kontextprüfungen…</p>}{error && <p role="alert" className="error-text">{error}</p>}
    {view && <>
      {!view.history.items.length && <p>Noch keine lesbare Kontextprüfung.</p>}
      {view.history.items.map((decision, index) => <details key={decision.id} open={index === 0} className="min-w-0 space-y-2">
        <summary className="cursor-pointer">Manueller Kontextvergleich · Version {decision.revision_number} · {new Date(decision.created_at).toLocaleString('de-DE')}</summary>
        <Snapshot value={decision.snapshot} /><p className="break-words">Angegebene Fundstelle: {decision.page_number === null ? decision.xml_path : `Seite ${decision.page_number}`}</p>
        <blockquote className="break-words">{decision.quotation}</blockquote><p className="break-words">{decision.comparison_note}</p>
        <p>Gespeicherte menschliche Angabe; keine automatische Identitätsprüfung und keine Kostenfreigabe.</p>
        {(decision.assignment_proposal_id !== proposalId || decision.invoice_review_id !== reviewId || decision.snapshot_digest !== preview?.snapshot_digest) && <p>Frühere Grundlage; nicht automatisch auf den aktuellen Stand übertragbar.</p>}
      </details>)}
      {view.history.next_before_revision && <p>Hier werden die letzten 20 lesbaren Kontextprüfungen angezeigt.</p>}
      {!canReview && <p>Für eine neue Kontextprüfung werden eine aktuelle Zuordnung, deren Feldprüfung und die nötigen Rechte für ein vollständiges WEG-/MIET-Objekt benötigt.</p>}
      {draft && draftAuthorized && (canReview || submitted) && <>
        {stale && !submitted && <div className="space-y-2"><p>Prüfen Sie die neue Grundlage. Beim Übernehmen werden die bisherigen Eingaben geleert.</p>
          <button className="btn" disabled={blocked || !canReview} onClick={() => { if (preview?.invoice_review_id) { setDraft(fresh(preview, view.history)); setConflict(false); setError(null); } }}>Neue Kontextgrundlage übernehmen</button></div>}
        <h3>Zu vergleichende Objekt- und Kreisangaben</h3><Snapshot value={draft.preview.snapshot} />
        <fieldset disabled={blocked || submitted || !canReview} className="space-y-3 min-w-0">
          <label className="block" htmlFor="context-locator">{draft.preview.page_start === null ? 'Angegebener XML-Pfad' : 'Seite der Objektangabe'}</label>
          <input id="context-locator" className={inputClass} maxLength={draft.preview.page_start === null ? 500 : 3} value={draft.locator} onChange={event => edit('locator', event.target.value)} />
          {draft.preview.page_start === null ? <p>Der Pfad ist Ihre Fundstellenangabe; er wird nicht automatisch gegen das XML geprüft.</p> : <p>Ausgewählter Beleg: Seiten {draft.preview.page_start}–{draft.preview.page_end}.</p>}
          <label className="block" htmlFor="context-quote">Zitat zur Objektzuordnung</label><textarea id="context-quote" className={inputClass} maxLength={2000} value={draft.quotation} onChange={event => edit('quotation', event.target.value)} />
          <label className="block" htmlFor="context-note">Begründung des Kontextvergleichs</label><textarea id="context-note" className={inputClass} maxLength={2000} value={draft.note} onChange={event => edit('note', event.target.value)} />
          <label className="flex items-start gap-2"><input type="checkbox" checked={draft.acknowledged} onChange={event => setDraft({ ...draft, acknowledged: event.target.checked })} />Objektangabe im ausgewählten Original mit den angezeigten Objekt- und Kreisangaben verglichen.</label>
        </fieldset>
        <button className="btn" disabled={blocked || (!submitted && (stale || !canReview || !draft.acknowledged || !draft.note.trim() || !draft.quotation.trim() || !draft.locator.trim()))} onClick={() => void save()}>{submitted ? 'Dieselbe Kontextprüfung erneut senden' : 'Kontextprüfung speichern'}</button>
      </>}
    </>}
  </section><AllocationProposalPanel logicalId={logicalId} contextDecisionId={latestDecision?.id ?? null}
    ready={canReview && latestDecision?.assignment_proposal_id === proposalId && latestDecision?.invoice_review_id === reviewId && latestDecision?.snapshot_digest === preview?.snapshot_digest}
    parentBusy={blocked || !view} parentGeneration={parentGeneration + generation} onDenied={onDenied} /></>;
}
