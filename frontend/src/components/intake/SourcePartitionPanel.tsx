import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { intakeApi, partitionApi, type LogicalDocument, type PartitionReview, type PartitionState } from '../../api/intake';
import { VisualExtractPanel } from './VisualExtractPanel';

const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60';
const denied = (failure: unknown) => failure instanceof ApiError && [401, 403, 404].includes(failure.status);
const range = (document: LogicalDocument) => document.page_start === null ? 'Gesamtes XML-Dokument' : document.page_start === document.page_end
  ? `Seite ${document.page_start}` : `Seiten ${document.page_start}–${document.page_end}`;
type Draft = { expected: string | null; selected: string[]; reason: string; acknowledged: boolean };
const draftFrom = (state: PartitionState): Draft => ({ expected: state.current?.id ?? null, selected: state.current?.logical_document_ids ?? [], reason: '', acknowledged: false });

type Props = { refId: string; documents: LogicalDocument[]; parentBusy: boolean; parentGeneration: number; onDenied: () => void; onCreated: () => void };

export function SourcePartitionPanel({ refId, documents, parentBusy, parentGeneration, onDenied, onCreated }: Props) {
  const [state, setState] = useState<PartitionState | null>(null);
  const [original, setOriginal] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [rangeBusy, setRangeBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const pending = useRef<{ key: string; body: PartitionReview; expected: string | null } | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const refresh = useCallback(() => setGeneration(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true);
    Promise.all([partitionApi.get(refId, controller.signal), partitionApi.original(refId, controller.signal)])
      .then(([value, source]) => { if (!controller.signal.aborted) {
        setState(value); setOriginal(source.source_kind === 'intake_original' ? source.source_id : null);
        setDraft(previous => previous ?? draftFrom(value));
      } }).catch(failure => { if (!controller.signal.aborted) {
        setState(null); setOriginal(null); setError('Die Aufteilung ist derzeit nicht verfügbar. Bitte erneut laden.');
        if (denied(failure)) onDenied();
      } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [refId, generation, parentGeneration, onDenied]);
  const stale = conflict || !!state && !!draft && (state.current?.id ?? null) !== draft.expected;
  const selected = documents.filter(item => draft?.selected.includes(item.id));
  const pages = documents[0]?.verified_page_count;
  const complete = !!draft && selected.length === draft.selected.length && selected.length > 0 && (pages === null
    ? selected.length === 1 && selected[0].page_start === null
    : Number.isInteger(pages) && Array.from({ length: pages }, (_, index) => index + 1)
      .every(page => selected.filter(item => item.page_start !== null && item.page_end !== null && item.page_start <= page && item.page_end >= page).length === 1));
  const blocked = busy || parentBusy || sending || rangeBusy;
  async function save() {
    if (blocked || !state?.can_review || !draft || (!pending.current && (stale || !complete || !draft.reason.trim() || !draft.acknowledged))) return;
    pending.current ??= { key: crypto.randomUUID(), expected: draft.expected,
      body: { logical_document_ids: draft.selected, reason: draft.reason.trim(), visual_pages_reviewed: true } };
    const request = pending.current; setSending(true); setSubmitted(true); setError(null); setSaved(null);
    try {
      const result = await partitionApi.review(refId, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setConflict(false); setDraft(null); setSaved(result.revision_number); refresh(); onCreated();
    } catch (failure) {
      if (!alive.current) return;
      if (denied(failure)) { setState(null); setOriginal(null); onDenied(); }
      else if (failure instanceof ApiError && [409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if (failure.status === 409) { setConflict(true); refresh(); }
        setError(failure.status === 409 ? 'Die Aufteilung wurde inzwischen geändert. Ihre Eingaben bleiben erhalten.' : 'Die Aufteilung konnte nicht gespeichert werden. Bitte Seiten und Eingaben prüfen.');
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Aufteilung erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="card min-w-0 space-y-4" aria-label="Geprüfte Seitenaufteilung">
    <div className="flex flex-wrap justify-between gap-3"><h2>Seiten zu Belegen zusammenstellen</h2>
      <button className="btn" disabled={blocked} onClick={() => { setError(null); refresh(); }}>Aufteilung aktualisieren</button></div>
    <p>Ordnen Sie jede sichtbare Seite genau einem Beleg zu. Prüfen Sie die Grenzen anhand des Originals. Eingebettete XML-Daten und Anlagen werden hier nicht bewertet; Feldwerte und Kosten bleiben ungeprüft.</p>
    {busy && <p role="status">Lade geprüfte Aufteilung…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {saved !== null && <p role="status">Aufteilung als Version {saved} gespeichert.</p>}
    {state && <>
      {!blocked && state.can_export && original && <a className="btn" href={intakeApi.content(original)} target="_blank" rel="noreferrer">Original zur Prüfung öffnen</a>}
      {!state.can_export && <p>Für das Öffnen des Originals fehlt die Exportberechtigung.</p>}
      {state.current ? <div className="space-y-1"><h3>Geprüfte Aufteilung · Version {state.current.revision_number}</h3>
        <p>{state.current.logical_document_ids.map(id => documents.find(item => item.id === id)).filter((item): item is LogicalDocument => !!item).map(range).join(' · ')}</p>
        <p className="break-words">{state.current.reason}</p></div> : <p>Noch keine geprüfte Aufteilung.</p>}
      {!state.can_review && <p>Die Aufteilung ist derzeit nur lesbar.</p>}
      {state.can_review && draft && <>
        {stale && !submitted && <div className="space-y-2"><p>Für eine neue Prüfung übernehmen Sie zuerst den aktuellen Stand. Dabei werden die Eingaben in diesem Formular zurückgesetzt.</p>
          <button className="btn" disabled={blocked} onClick={() => { setDraft(draftFrom(state)); setConflict(false); setError(null); setSaved(null); }}>Neueste Aufteilung übernehmen</button></div>}
        <fieldset disabled={blocked || submitted} className="min-w-0 space-y-3">
          <legend className="font-semibold">Belege für die Aufteilung auswählen</legend>
          <div className="max-h-64 overflow-auto space-y-2">{documents.map(item => <label key={item.id} className="flex items-start gap-2">
            <input type="checkbox" aria-label={`${range(item)} in Aufteilung aufnehmen`} checked={draft.selected.includes(item.id)} onChange={event => {
              setDraft({ ...draft, selected: event.target.checked ? [...draft.selected, item.id] : draft.selected.filter(id => id !== item.id) }); setSaved(null);
            }} /><span>{range(item)}</span></label>)}</div>
          <p>{complete ? 'Jede sichtbare Seite ist genau einmal enthalten.' : 'Die Auswahl muss alle sichtbaren Seiten ohne Lücken oder Überschneidungen enthalten.'}</p>
          <label className="block" htmlFor="partition-reason">Begründung der Aufteilung</label>
          <textarea id="partition-reason" className={inputClass} maxLength={1000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })} />
          <label className="flex items-start gap-2"><input type="checkbox" checked={draft.acknowledged} onChange={event => setDraft({ ...draft, acknowledged: event.target.checked })} />
            <span>Alle sichtbaren Seiten im Original geprüft und die Beleggrenzen bewusst gewählt.</span></label>
        </fieldset>
        <button className="btn" disabled={blocked || (!submitted && (stale || !complete || !draft.reason.trim() || !draft.acknowledged))} onClick={() => void save()}>
          {submitted ? 'Dieselbe Aufteilung erneut senden' : 'Aufteilung speichern'}</button>
      </>}
    </>}
    {pages !== null && pages > 0 && <VisualExtractPanel key={`extract-${refId}`} documents={documents}
      partitionId={state?.current?.id ?? null} memberIds={state?.current?.logical_document_ids ?? []}
      canRead={!!state?.can_export} canGenerate={!!state?.can_review && !!state?.can_export}
      parentGeneration={generation + parentGeneration} disabled={blocked || submitted} />}
    {pages !== null && pages > 0 && <div hidden={!state?.can_review || !draft}>
      <RangeCreator refId={refId} documents={documents} pages={pages} disabled={blocked || submitted || stale || !state?.can_review || !draft}
        onDenied={onDenied} onCreated={onCreated} onBusy={setRangeBusy} />
    </div>}
  </section>;
}

function RangeCreator({ refId, documents, pages, disabled, onDenied, onCreated, onBusy }: {
  refId: string; documents: LogicalDocument[]; pages: number; disabled: boolean; onDenied: () => void; onCreated: () => void; onBusy: (busy: boolean) => void;
}) {
  const [start, setStart] = useState(''); const [end, setEnd] = useState(''); const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false); const [submitted, setSubmitted] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ key: string; body: { document_ref_id: string; page_start: number; page_end: number; reason: string } } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const first = Number(start), last = Number(end);
  const exists = documents.some(item => item.page_start === first && item.page_end === last);
  const valid = Number.isInteger(first) && Number.isInteger(last) && first >= 1 && last >= first && last <= pages && !!reason.trim() && !exists && documents.length < 200;
  async function create() {
    if (disabled || busy || (!pending.current && !valid)) return;
    pending.current ??= { key: crypto.randomUUID(), body: { document_ref_id: refId, page_start: first, page_end: last, reason: reason.trim() } };
    setBusy(true); onBusy(true); setSubmitted(true); setError(null);
    try {
      await partitionApi.createRange(pending.current.body, pending.current.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setStart(''); setEnd(''); setReason(''); onCreated();
    } catch (failure) {
      if (!alive.current) return;
      if (denied(failure)) onDenied();
      else if (failure instanceof ApiError && [409, 422].includes(failure.status)) {
        pending.current = null; setSubmitted(false); setError('Seitenbereich nicht angelegt. Bitte vorhandene Belege und Seitenangaben prüfen.'); onCreated();
      } else setError('Keine bestätigte Antwort erhalten. Wiederholen Sie denselben Seitenbereich.');
    } finally { if (alive.current) { setBusy(false); onBusy(false); } }
  }
  return <details className="space-y-3"><summary className="cursor-pointer">Weiteren Seitenbereich anlegen</summary>
    <p>Seiten beziehen sich auf ihre Reihenfolge im Original. Ein neuer Bereich bleibt ein ungeprüfter Vorschlag und wird für die Texterkennung vorgemerkt.</p>
    <fieldset disabled={disabled || busy || submitted} className="space-y-3">
      <div className="grid grid-cols-2 gap-3"><label>Erste Seite<input className={inputClass} type="number" min={1} max={pages} value={start} onChange={event => setStart(event.target.value)} /></label>
        <label>Letzte Seite<input className={inputClass} type="number" min={1} max={pages} value={end} onChange={event => setEnd(event.target.value)} /></label></div>
      <label className="block">Grund für den Seitenbereich<input className={inputClass} maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label>
    </fieldset>
    {exists && !submitted && <p>Dieser Seitenbereich ist bereits in der Auswahl vorhanden.</p>}
    {documents.length >= 200 && <p>Die Höchstzahl von 200 Belegvorschlägen ist erreicht.</p>}
    <button className="btn" disabled={disabled || busy || (!submitted && !valid)} onClick={() => void create()}>{submitted ? 'Denselben Seitenbereich erneut senden' : 'Seitenbereich anlegen'}</button>
    {error && <p role="alert" className="error-text">{error}</p>}
  </details>;
}
