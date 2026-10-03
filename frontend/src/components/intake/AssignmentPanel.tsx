import { useEffect, useRef, useState } from 'react';
import { ContextCandidatesPanel } from './ContextCandidatesPanel';
import { InvoiceReviewPanel } from './InvoiceReviewPanel';
import { ApiError } from '../../api/client';
import { assignmentApi, partitionApi, type AssignmentCreate, type AssignmentProposal, type PartitionState, type ExtractionRevision } from '../../api/intake';
import { SearchChoice } from '../SearchChoice';

type Scope = { id: string; label: string; scope_type: string };
type View = { partition: PartitionState; items: AssignmentProposal[]; more: boolean };
type Draft = { expected: string | null; partition: string | null; extraction: string | null; scope: Scope | null; reason: string };
const fresh = (view: View, extraction: string | null): Draft => ({ expected: view.items[0]?.id ?? null, partition: view.partition.current?.id ?? null, extraction, scope: null, reason: '' });
const blockers: Record<string, string> = { scope_hint_conflict: 'Upload-Kontext und vorgeschlagener Abrechnungskreis weichen voneinander ab.', unsupported_scope_type: 'Für diese Abrechnungsart ist die finanzielle Übernahme noch nicht verfügbar.' };

export function AssignmentPanel({ logicalId, refId, extractionId, evidenceReady, parentBusy, parentGeneration, onDenied, extraction }: {
  extraction?: ExtractionRevision | null; logicalId: string; refId: string; extractionId: string | null; evidenceReady: boolean; parentBusy: boolean; parentGeneration: number; onDenied: () => void;
}) {
  const [view, setView] = useState<View | null>(null); const [draft, setDraft] = useState<Draft | null>(null);
  const [generation, setGeneration] = useState(0); const [busy, setBusy] = useState(false); const [sending, setSending] = useState(false);
  const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ key: string; body: AssignmentCreate; expected: string | null } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true);
    Promise.all([partitionApi.get(refId, controller.signal), assignmentApi.history(logicalId, controller.signal)]).then(([partition, history]) => {
      if (controller.signal.aborted) return;
      const value = { partition, items: history.items, more: history.next_before_revision !== null };
      setView(value); setDraft(previous => previous ?? (extractionId ? fresh(value, extractionId) : null));
    }).catch(failure => { if (!controller.signal.aborted) {
      setView(null); setError('Zuordnung derzeit nicht verfügbar. Bitte erneut laden.');
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) onDenied();
    } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [logicalId, refId, extractionId, generation, parentGeneration, onDenied]);
  const stale = conflict || !!view && !!draft && (draft.expected !== (view.items[0]?.id ?? null)
    || draft.partition !== (view.partition.current?.id ?? null) || draft.extraction !== extractionId);
  const member = !!view?.partition.current?.logical_document_ids.includes(logicalId);
  const blocked = busy || parentBusy || sending;
  const ready = member && evidenceReady && !!extractionId;
  async function save() {
    if (blocked || !view?.partition.can_review || !draft || (!pending.current && (stale || !ready || !draft.scope || !draft.reason.trim()))) return;
    if (!pending.current && draft.scope && draft.partition && draft.extraction) pending.current = { key: crypto.randomUUID(), expected: draft.expected,
      body: { partition_revision_id: draft.partition, extraction_revision_id: draft.extraction, scope_id: draft.scope.id, reason: draft.reason.trim() } };
    if (!pending.current) return;
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      await assignmentApi.create(logicalId, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setConflict(false); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if ([401, 403, 404].includes(failure.status)) { setView(null); setDraft(null); }
        else if (failure.status === 409) setConflict(true);
        setError(failure.status === 409 ? 'Der Bearbeitungsstand hat sich geändert. Ihre Eingaben bleiben erhalten.'
          : 'Vorschlag nicht gespeichert. Bitte Grundlage und Berechtigung des Abrechnungskreises prüfen.');
        setGeneration(value => value + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie denselben Zuordnungsvorschlag erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="card min-w-0 space-y-4" aria-label="Zuordnungsvorschläge">
    <div className="flex flex-wrap justify-between gap-3"><h2>Abrechnungskreis vorschlagen</h2>
      <button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Zuordnung aktualisieren</button></div>
    <p>Wählen Sie den Kontext nach eigener Prüfung. Dieser Vorschlag bestätigt weder Feldwerte noch Kosten und gibt das Original nicht für weitere Personen frei.</p>
    {busy && <p role="status">Lade Zuordnungsvorschläge…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {view && <>
      {!member && <p>Dieser Beleg gehört noch nicht zur aktuellen geprüften Aufteilung.</p>}
      {!evidenceReady && <p>Für einen neuen Vorschlag wird ein verfügbarer Extraktionsstand benötigt.</p>}
      {!view.partition.can_review && <p>Die Zuordnung ist derzeit nur lesbar.</p>}
      {!view.items.length && <p>Noch kein lesbarer Zuordnungsvorschlag.</p>}
      {view.items.map((item, index) => <details key={item.id} className="min-w-0 space-y-2" open={index === 0}>
        <summary className="cursor-pointer">Manuell vorgeschlagen · Version {item.revision_number}{item.state === 'blocked' ? ' · Klärung erforderlich' : ' · ungeprüft'}</summary>
        <p>{item.scope_label} · {item.scope_type}</p><p className="break-words">{item.reason}</p>
        {item.blockers.map(code => <p key={code}>{blockers[code] ?? 'Dieser Vorschlag benötigt weitere Klärung.'}</p>)}
        {(item.partition_revision_id !== view.partition.current?.id || item.extraction_revision_id !== extractionId) && <p>Dieser Vorschlag bezieht sich auf eine frühere Grundlage. Er wurde nicht automatisch angepasst.</p>}
      </details>)}
      {view.more && <p>Hier werden die letzten 20 lesbaren Vorschläge angezeigt.</p>}
      {view.partition.can_review && draft && <>
        {stale && !submitted && <div className="space-y-2"><p>Die aktuelle Grundlage muss ausdrücklich übernommen werden. Ihre Formulareingaben werden dabei zurückgesetzt.</p>
          <button className="btn" disabled={blocked} onClick={() => { setDraft(fresh(view, extractionId)); setConflict(false); setError(null); }}>Aktuelle Grundlage übernehmen</button></div>}
        <ContextCandidatesPanel logicalId={logicalId} partitionId={view.partition.current?.id ?? null} extractionId={extractionId}
          disabled={blocked || submitted || stale || !ready} parentGeneration={parentGeneration + generation}
          onPick={candidate => { if (!blocked && !submitted && !stale && ready) setDraft({ ...draft,
            scope: { id: candidate.scope_id, label: candidate.scope_label, scope_type: candidate.scope_type },
            reason: draft.reason || `Belegangabe auf Seite ${candidate.evidence[0].page_number}: ${candidate.evidence[0].quotation}`.slice(0, 1000) }); }} />
        <fieldset disabled={blocked || submitted || !ready} className="space-y-3 min-w-0">
          <SearchChoice<Scope> label="Vorgeschlagener Abrechnungskreis" endpoint="/api/accounting-scopes/page?capability=prepare&financial_creation=true"
            value={draft.scope} onChange={scope => setDraft({ ...draft, scope })} display={scope => `${scope.label} · ${scope.scope_type}`} />
          <label className="block" htmlFor="assignment-reason">Begründung des Zuordnungsvorschlags</label>
          <textarea id="assignment-reason" className="w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60" maxLength={1000}
            value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })} />
        </fieldset>
        <button className="btn" disabled={blocked || (!submitted && (stale || !ready || !draft.scope || !draft.reason.trim()))} onClick={() => void save()}>
          {submitted ? 'Denselben Zuordnungsvorschlag erneut senden' : 'Zuordnung vorschlagen'}</button>
      </>}
    </>}
    {extraction !== undefined && <InvoiceReviewPanel logicalId={logicalId} proposal={view?.items[0] ?? null} extraction={extraction}
      ready={ready && view?.items[0]?.partition_revision_id === view?.partition.current?.id && view?.items[0]?.extraction_revision_id === extractionId}
      parentBusy={blocked || !view} parentGeneration={parentGeneration + generation} onDenied={onDenied} />}
  </section>;
}
