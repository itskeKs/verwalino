import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { allocationAdoptionsApi, type AdoptionCommand, type AdoptionPreview, type AllocationSet, type CorrectionResult } from '../../api/allocationAdoptions';
import { VisualAllocationAdoptionPanel } from './VisualAllocationAdoptionPanel';
import { ContextMappingPanel } from './ContextMappingPanel';
import { AllocationCorrectionPanel } from './AllocationCorrectionPanel';

type Draft = { review: string; preview: AdoptionPreview; reason: string; costs: boolean; disclosure: boolean };
const denied = (error: unknown) => error instanceof ApiError && [401, 403, 404].includes(error.status);
const fresh = (review: string, preview: AdoptionPreview): Draft => ({ review, preview, reason: '', costs: false, disclosure: false });

export function AllocationAdoptionPanel({ logicalId, reviewId, ready, parentBusy, parentGeneration }: {
  logicalId: string; reviewId: string | null; ready: boolean; parentBusy: boolean; parentGeneration: number;
}) {
  const [view, setView] = useState<{ preview: AdoptionPreview | null; authorizedReview: string | null; result: AllocationSet | null; correction?: CorrectionResult | null } | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null); const [busy, setBusy] = useState(true); const [sending, setSending] = useState(false);
  const [generation, setGeneration] = useState(0); const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false); const [error, setError] = useState<string | null>(null);
  const [correctionDenial, setCorrectionDenial] = useState(0);
  const [sourceVariant, setSourceVariant] = useState<'original' | 'visual'>('original');
  const [visualPending, setVisualPending] = useState(false);
  const pending = useRef<{ body: AdoptionCommand; key: string } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const retained = draft?.review ?? null;
  useEffect(() => {
    const controller = new AbortController(); setBusy(true);
    const current = reviewId ? allocationAdoptionsApi.preview(reviewId, controller.signal) : Promise.resolve(null);
    const old = retained && retained !== reviewId ? allocationAdoptionsApi.preview(retained, controller.signal) : current;
    Promise.allSettled([allocationAdoptionsApi.result(logicalId, controller.signal), current, old]).then(results => {
      if (controller.signal.aborted) return;
      const [receipt, preview, previous] = results;
      if (receipt.status === 'rejected' && denied(receipt.reason)) setCorrectionDenial(value => value + 1);
      if (receipt.status === 'rejected' || previous.status === 'rejected' || preview.status === 'rejected') {
        if ((receipt.status === 'rejected' && denied(receipt.reason)) || (previous.status === 'rejected' && denied(previous.reason))) {
          pending.current = null; setSubmitted(false); setDraft(null);
        }
        setView(receipt.status === 'fulfilled' && previous.status === 'fulfilled' ? { result: receipt.value.item ?? null, correction: receipt.value.correction ?? null, preview: null, authorizedReview: previous.value ? retained ?? reviewId : null } : null);
        setError('Übernahmeprüfung derzeit nicht vollständig verfügbar. Bitte erneut laden.');
      } else {
        setView({ result: receipt.value.item ?? null, correction: receipt.value.correction ?? null, preview: preview.value, authorizedReview: previous.value ? retained ?? reviewId : null });
        if (reviewId && preview.value && ready && !receipt.value.item) setDraft(prior => prior ?? fresh(reviewId, preview.value!));
      }
      setBusy(false);
    });
    return () => controller.abort();
  }, [logicalId, reviewId, retained, ready, generation, parentGeneration]);
  const baseBlocked = busy || parentBusy || sending;
  const blocked = baseBlocked || visualPending;
  const eligible = ready && !!view?.preview?.can_adopt && !view.result;
  const authorized = !!draft && view?.authorizedReview === draft.review;
  const stale = conflict || !!draft && (draft.review !== reviewId || draft.preview.preview_digest !== view?.preview?.preview_digest);
  async function save() {
    if (blocked || !authorized || !draft || (!pending.current && (!eligible || stale || !draft.costs || !draft.disclosure || !draft.reason.trim() || !draft.preview.preview_digest))) return;
    if (!pending.current) pending.current = { key: crypto.randomUUID(), body: { treatment_review_id: draft.review, preview_digest: draft.preview.preview_digest!, reason: draft.reason.trim(), costs_confirmed: true, whole_original_disclosure_reviewed: true } };
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      const result = await allocationAdoptionsApi.create(logicalId, request.body, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setConflict(false);
      setView({ preview: null, authorizedReview: null, result }); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if (denied(failure)) { setView(null); setDraft(null); }
        if (failure.status === 409) setConflict(true);
        setError(failure.status === 409 ? 'Die Übernahmegrundlage hat sich geändert. Prüfen Sie den aktuellen Stand erneut.' : 'Übernahme nicht bestätigt. Bitte Angaben und Berechtigung prüfen.'); setGeneration(value => value + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Kostenübernahme erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="min-w-0 space-y-4 border-t border-input pt-4" aria-label="Kosten übernehmen">
    <div className="flex flex-wrap justify-between gap-3"><h2>Kosten übernehmen</h2><button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Übernahme aktualisieren</button></div>
    <p>Mit der Übernahme werden alle geprüften Teilkosten verbindlich bestätigt. Wählen Sie den Beleg, der im Abrechnungskreis aufbewahrt werden soll.</p>
    {!view?.result && <fieldset className="flex flex-wrap gap-4" disabled={blocked || submitted}><legend>Beleg für die Kostenübernahme</legend>
      <label><input type="radio" name={`financial-source-${logicalId}`} checked={sourceVariant === 'original'} onChange={() => setSourceVariant('original')} /> Vollständiges Original</label>
      <label><input type="radio" name={`financial-source-${logicalId}`} checked={sourceVariant === 'visual'} onChange={() => setSourceVariant('visual')} /> Freigegebener Auszug</label>
    </fieldset>}
    {busy && <p role="status">Prüfe Kostenübernahme…</p>}{error && <p role="alert" className="error-text">{error}</p>}
    {view?.result && <div className="space-y-3" role="status"><h3>Kostenübernahme bestätigt</h3><p>{new Date(view.result.confirmed_at).toLocaleString('de-DE')} · {view.result.cost_count} bestätigte Teilkosten</p>
      <ol className="space-y-2">{view.result.costs.map(cost => <li key={cost.cost_entry_id}><a className="underline" href={`/kosten/${cost.cost_entry_id}`}>Teilkosten {cost.position}: {cost.gross_amount} EUR</a><span> · Mieteranteil {cost.tenant_amount ?? 'nicht angegeben'} EUR</span></li>)}</ol>
      <a className="block underline" href={`/belege/${view.result.source_document_id}`}>{view.result.source_variant === 'visual' ? 'Dauerhaft aufbewahrten Auszug zu allen Teilkosten öffnen' : 'Freigegebenes Original zu allen Teilkosten öffnen'}</a><p className="break-words">{view.result.reason}</p></div>}
    {sourceVariant === 'original' && view && !view.result && !eligible && <p>Übernahme derzeit nicht möglich. Aktuelle Berechtigungen, vollständige Behandlung und offene Belegkonflikte müssen geklärt sein.</p>}
    {sourceVariant === 'original' && draft && authorized && (eligible || submitted) && <>
      {stale && !submitted && <div><p>Die bisherige Bestätigung gilt nicht für die geänderte Grundlage.</p><button className="btn" disabled={blocked || !eligible} onClick={() => { if (reviewId && view?.preview) { setDraft(fresh(reviewId, view.preview)); setConflict(false); setError(null); } }}>Aktuelle Übernahmegrundlage prüfen</button></div>}
      <p className="break-words">Abrechnungskreis: {draft.preview.scope_label ?? 'aus der geprüften Zuordnung'} · {draft.preview.cost_count} Teilkosten · Gesamtbetrag {draft.preview.gross_amount} EUR</p>
      <p>Das vollständige Original wird für die zum Beleg berechtigten Personen dieses Abrechnungskreises zugänglich. Prüfen Sie auch alle weiteren Seiten und enthaltenen Angaben.</p>
      <fieldset className="min-w-0 space-y-3" disabled={blocked || submitted || stale || !eligible}>
        <label className="block" htmlFor="adoption-reason">Begründung der Kostenübernahme</label><textarea id="adoption-reason" className="w-full min-w-0 rounded-md border border-input bg-background px-3 py-2" maxLength={2000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value, costs: false, disclosure: false })} />
        <label className="flex items-start gap-2"><input type="checkbox" checked={draft.costs} onChange={event => setDraft({ ...draft, costs: event.target.checked })} />Alle Teilkosten, Beträge und Verteilungen geprüft; verbindlich bestätigen.</label>
        <label className="flex items-start gap-2"><input type="checkbox" checked={draft.disclosure} onChange={event => setDraft({ ...draft, disclosure: event.target.checked })} />Vollständiges Original geprüft; Freigabe für diesen Abrechnungskreis bestätigen.</label>
      </fieldset>
      <button className="btn" disabled={blocked || (!submitted && (!eligible || stale || !draft.costs || !draft.disclosure || !draft.reason.trim()))} onClick={() => void save()}>{submitted ? 'Dieselbe Kostenübernahme erneut senden' : 'Teilkosten bestätigen und Original freigeben'}</button>
    </>}
    <VisualAllocationAdoptionPanel logicalId={logicalId} reviewId={reviewId} enabled={sourceVariant === 'visual' && (!view?.result || visualPending)} parentBusy={baseBlocked}
      generation={parentGeneration + generation} onPending={setVisualPending} onConfirmed={() => setGeneration(value => value + 1)} />
    <div hidden={!view?.result}><AllocationCorrectionPanel sourceVariant={view?.result?.source_variant} key={`${logicalId}:${correctionDenial}`} allocationId={view?.result?.id ?? null} reviewId={reviewId} parentBusy={blocked || !view?.result} parentGeneration={parentGeneration + generation} receipt={view?.correction ?? null} onConfirmed={() => setGeneration(value => value + 1)} /></div>
    <ContextMappingPanel allocationId={view?.result?.id ?? null} parentGeneration={parentGeneration + generation} parentBusy={blocked} />
  </section>;
}
