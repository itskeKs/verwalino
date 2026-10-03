import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { exceptionApi, exceptionActions, exceptionCauses, type DuplicateExceptionDependency, type ExceptionAction, type ExceptionCase, type ExceptionCommand, type ExceptionDependency, type ExceptionEvent, type RevisionPage } from '../api/exceptions';
import { useAuth } from '../auth/useAuth';

type View = { item: ExceptionCase; events: RevisionPage<ExceptionEvent>; dependencies: RevisionPage<ExceptionDependency>; duplicates: RevisionPage<DuplicateExceptionDependency> };
type Draft = { expected: string; action: ExceptionAction; reason: string; until: string };
const fresh = (item: ExceptionCase): Draft => ({ expected: item.revision_id, action: 'claim', reason: '', until: '' });
const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60';
const denied = (error: unknown) => error instanceof ApiError && [401, 403, 404].includes(error.status);

export function ExceptionDetailPage() {
  const { caseId = '' } = useParams(); return <ExceptionDetail key={caseId} id={caseId} />;
}

function ExceptionDetail({ id }: { id: string }) {
  const { session } = useAuth();
  const [view, setView] = useState<View | null>(null); const [draft, setDraft] = useState<Draft | null>(null);
  const [generation, setGeneration] = useState(0); const [busy, setBusy] = useState(true); const [sending, setSending] = useState(false);
  const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null);
  const pending = useRef<{ key: string; expected: string; body: ExceptionCommand } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setView(null);
    Promise.all([exceptionApi.get(id, controller.signal), exceptionApi.events(id, undefined, controller.signal), exceptionApi.dependencies(id, undefined, controller.signal), exceptionApi.duplicateDependencies(id, undefined, controller.signal)])
      .then(([item, events, dependencies, duplicates]) => { if (!controller.signal.aborted) { setView({ item, events, dependencies, duplicates }); setDraft(previous => previous ?? fresh(item)); } })
      .catch(failure => { if (!controller.signal.aborted) {
        setError(denied(failure) ? 'Dieser Fall ist nicht mehr zugänglich.' : 'Fall derzeit nicht verfügbar. Bitte erneut laden.');
        if (denied(failure)) { setDraft(null); pending.current = null; setSubmitted(false); setNotice(null); }
      } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [id, generation]);
  const stale = conflict || !!draft && !!view && draft.expected !== view.item.revision_id;
  const blocked = busy || sending;
  const ownClaim = !!view?.item.claimant_id && view.item.claimant_id === session?.user_id;
  async function save() {
    if (blocked || !view?.item.can_work || !draft || (!pending.current && (stale || !draft.reason.trim()))) return;
    if (!pending.current) {
      const time = draft.action === 'snooze' ? new Date(draft.until) : null;
      if (time && (!Number.isFinite(time.getTime()) || time.getTime() <= Date.now())) { setError('Bitte einen zukünftigen Zeitpunkt zum Zurückstellen wählen.'); return; }
      pending.current = { key: crypto.randomUUID(), expected: draft.expected,
        body: { action: draft.action, reason: draft.reason.trim(), snoozed_until: time?.toISOString() ?? null } };
    }
    const request = pending.current; setSending(true); setSubmitted(true); setError(null); setNotice(null);
    try {
      const receipt = await exceptionApi.append(id, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setConflict(false);
      setNotice(`${exceptionActions[receipt.action]} · Beleg für Fallversion ${receipt.revision_number}. Der aktuelle Stand wird neu geladen.`);
      setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (denied(failure)) { pending.current = null; setSubmitted(false); setView(null); setDraft(null); setError('Dieser Fall ist nicht mehr bearbeitbar. Bitte Berechtigung und Stand erneut prüfen.'); }
      else if (failure instanceof ApiError && [409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if (failure.status === 409) { setConflict(true); setGeneration(value => value + 1); }
        setError(failure.status === 409 ? 'Der Fall wurde geändert. Ihre Eingaben bleiben erhalten; prüfen Sie die neue Grundlage.' : 'Aktion nicht gespeichert. Bitte Aktion, Begründung und Zeitpunkt prüfen.');
      } else setError('Keine bestätigte Antwort erhalten. Wiederholen Sie denselben Auftrag.');
    } finally { if (alive.current) setSending(false); }
  }
  async function older(kind: 'events' | 'dependencies' | 'duplicates') {
    const before = view?.[kind].next_before_revision;
    if (!before || blocked || !view) return;
    setBusy(true); setError(null);
    try {
      if (kind === 'events') {
        const page = await exceptionApi.events(id, before);
        if (alive.current) setView(value => value ? { ...value, events: page } : null);
      } else if (kind === 'duplicates') {
        const page = await exceptionApi.duplicateDependencies(id, before);
        if (alive.current) setView(value => value ? { ...value, duplicates: page } : null);
      } else {
        const page = await exceptionApi.dependencies(id, before);
        if (alive.current) setView(value => value ? { ...value, dependencies: page } : null);
      }
    } catch (failure) {
      if (!alive.current) return;
      setView(null); setError('Historie derzeit nicht verfügbar. Bitte den Fall erneut laden.');
      if (denied(failure)) { setDraft(null); pending.current = null; setSubmitted(false); setNotice(null); }
    } finally { if (alive.current) setBusy(false); }
  }
  return <div className="min-w-0 space-y-5">
    <Link to="/klaerungsfaelle">Alle Klärungsfälle</Link>
    <header className="flex flex-wrap justify-between gap-3"><h1>Klärungsfall</h1>
      <button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Fall aktualisieren</button></header>
    {busy && <p role="status">Lade Fall und Historie…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {view && <>
      <section className="card min-w-0 space-y-3" aria-label="Fallgrundlage">
        <h2>{exceptionCauses[view.item.cause] ?? 'Klärung erforderlich'}</h2>
        <p className="break-words">{view.item.scope_label} · {view.item.scope_type}</p>
        <p>{view.item.workflow_state === 'snoozed' ? `Zurückgestellt bis ${new Date(view.item.snoozed_until!).toLocaleString('de-DE')}` : 'Offen'} · Fachlich ungeklärt · Version {view.item.revision_number}</p>
        <p>{ownClaim ? 'Von Ihnen beansprucht' : view.item.claimant_id ? 'Bearbeitung beansprucht' : 'Noch nicht beansprucht'}</p>
        {view.item.dependency_count > 0 && <p>{view.item.dependency_count} verknüpfte Zuordnungsvorschläge. Frühere Vorschläge bleiben als Belege erhalten.</p>}
        {!!view.item.duplicate_dependency_count && <p>{view.item.duplicate_dependency_count} Dublettenhinweise auf konkrete Prüfstände. Frühere Hinweise bleiben erhalten; ein Belegvergleich schließt den Fall nicht ab.</p>}
        <Link to={`/posteingang/belege/${view.item.document_ref_id}?logical_document_id=${encodeURIComponent(view.item.logical_document_id)}&extraction_revision_id=${encodeURIComponent(view.item.extraction_revision_id)}`}>Original und Zuordnungsvorschläge prüfen</Link>
      </section>
      <section className="card min-w-0 space-y-3" aria-label="Bearbeitung dokumentieren">
        <h2>Bearbeitung dokumentieren</h2>
        <p>Ein Anspruch dient der Abstimmung und sperrt den Fall nicht für andere Berechtigte. Zurückstellen hebt die fachliche Sperre nicht auf. Neue Hinweise öffnen die Bearbeitung erneut.</p>
        {!view.item.can_work && <p>Dieser Fall ist derzeit nur lesbar.</p>}
        {view.item.can_work && draft && <>
          {stale && !submitted && <div className="space-y-2"><p>Prüfen Sie den aktuellen Fallstand, bevor Sie Ihre Aktion darauf anwenden.</p>
            <button className="btn" disabled={blocked} onClick={() => { setDraft({ ...draft, expected: view.item.revision_id }); setConflict(false); setError(null); }}>Aktuellen Fallstand übernehmen</button></div>}
          <fieldset disabled={blocked || submitted} className="space-y-3 min-w-0">
            <label className="block" htmlFor="exception-action">Aktion</label>
            <select id="exception-action" className={inputClass} value={draft.action} onChange={event => setDraft({ ...draft, action: event.target.value as ExceptionAction })}>
              <option value="claim">Für mich beanspruchen</option><option value="release" disabled={!ownClaim}>Meinen Anspruch freigeben</option>
              <option value="snooze">Zurückstellen</option><option value="reopen">Zur Bearbeitung wieder öffnen</option>
            </select>
            {draft.action === 'snooze' && <><label className="block" htmlFor="exception-until">Zurückstellen bis (Ortszeit)</label>
              <input id="exception-until" className={inputClass} type="datetime-local" value={draft.until} onChange={event => setDraft({ ...draft, until: event.target.value })} /></>}
            <label className="block" htmlFor="exception-reason">Begründung der Aktion</label>
            <textarea id="exception-reason" className={inputClass} maxLength={1000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })} />
          </fieldset>
          <button className="btn" disabled={blocked || (!submitted && (stale || !draft.reason.trim() || (draft.action === 'release' && !ownClaim)))} onClick={() => void save()}>
            {submitted ? 'Denselben Auftrag wiederholen' : 'Aktion speichern'}</button>
        </>}
      </section>
      <section className="card min-w-0 space-y-3" aria-label="Bearbeitungshistorie"><h2>Bearbeitungshistorie</h2>
        <p>Die Einträge zeigen den Stand zum jeweiligen Zeitpunkt.</p>
        <ol className="space-y-3">{view.events.items.map(event => <li key={event.id} className="min-w-0 border-b border-input pb-3 space-y-1">
          <h3>{exceptionActions[event.action]} · Version {event.revision_number}</h3>
          <p>{new Date(event.created_at).toLocaleString('de-DE')} · {event.actor_id === null ? event.action.startsWith('duplicate_') ? 'Automatisch verknüpft; Zeitpunkt der Erfassung' : 'Automatisch aus Vorschlag' : event.actor_id === session?.user_id ? 'Von Ihnen' : 'Manuelle Bearbeitung'}</p>
          {event.actor_id !== null && <p className="break-words">{event.reason}</p>}
          {event.snoozed_until && <p>Damals zurückgestellt bis {new Date(event.snoozed_until).toLocaleString('de-DE')}</p>}
        </li>)}</ol>
        {view.events.next_before_revision && <button className="btn" disabled={blocked} onClick={() => void older('events')}>Ältere Ereignisse</button>}
      </section>
      {!!view.item.duplicate_dependency_count && <section className="card min-w-0 space-y-3" aria-label="Verknüpfte Dublettenhinweise"><h2>Verknüpfte Dublettenhinweise</h2>
        <p>Hinweise und Vergleiche beziehen sich auf die damals erfassten Prüfstände. Neue oder nicht mehr übereinstimmende Feldwerte lösen den Fall nicht automatisch.</p>
        <ul className="space-y-4">{view.duplicates.items.map(dependency => <li key={dependency.id} className="space-y-2 border-b border-input pb-3 min-w-0">
          <h3>Dublettenhinweis · Fallversion {dependency.revision_number}</h3>
          <div className="grid gap-3 md:grid-cols-2"><div className="min-w-0"><p className="break-words">Beleg A: {dependency.anchor_seller_name} · {dependency.anchor_document_number}</p>
            <Link className="link" to={`/posteingang/belege/${dependency.anchor_document_ref_id}?logical_document_id=${encodeURIComponent(dependency.anchor_logical_id)}&extraction_revision_id=${encodeURIComponent(dependency.anchor_extraction_id)}`}>Beleg A am Original prüfen</Link></div>
            <div className="min-w-0"><p className="break-words">Beleg B: {dependency.reference_seller_name} · {dependency.reference_document_number}</p>
            <Link className="link" to={`/posteingang/belege/${dependency.reference_document_ref_id}?logical_document_id=${encodeURIComponent(dependency.reference_logical_id)}&extraction_revision_id=${encodeURIComponent(dependency.reference_extraction_id)}`}>Beleg B am Original prüfen</Link></div></div>
          <p>{dependency.evidence.match_reasons.includes('same_bytes_and_range') ? 'Identische Originaldatei und derselbe Seitenbereich.' : 'Übereinstimmende geprüfte Rechnungsmerkmale.'}</p>
          {dependency.evidence.kind_conflict && <p>Widersprüchliche Belegart: Rechnung und Gutschrift. Feldprüfungen korrigieren.</p>}
          {dependency.latest_exact_comparison ? <div className="space-y-1">
            <p>Letzter Vergleich dieser Prüfstände: {dependency.latest_exact_comparison.verdict === 'same_document' ? 'Derselbe Beleg' : dependency.latest_exact_comparison.verdict === 'distinct_documents' ? 'Verschiedene Belege' : dependency.latest_exact_comparison.subject_review_id === dependency.anchor_review_id ? 'Beleg A soll Beleg B ersetzen' : 'Beleg B soll Beleg A ersetzen'}.</p>
            <p className="break-words">{dependency.latest_exact_comparison.reason}</p><p>Historischer Prüfvermerk; keine Kostenfreigabe und kein Abschluss des Falls.</p>
          </div> : <p>Für diese Prüfstände ist noch kein menschlicher Vergleich verknüpft.</p>}
        </li>)}</ul>
        {view.duplicates.next_before_revision && <button className="btn" disabled={blocked} onClick={() => void older('duplicates')}>Ältere Dublettenhinweise</button>}
      </section>}
      {view.item.dependency_count > 0 && <section className="card min-w-0 space-y-3" aria-label="Verknüpfte Vorschläge"><h2>Verknüpfte Vorschläge</h2>
        <ul className="space-y-3">{view.dependencies.items.map(dependency => <li key={dependency.id} className="space-y-1"><h3>Zuordnungsvorschlag {dependency.proposal_revision_number}</h3><p className="break-words">{dependency.proposal_reason}</p><p>Erfasst mit Fallversion {dependency.revision_number} · {new Date(dependency.created_at).toLocaleString('de-DE')}</p></li>)}</ul>
        {view.dependencies.next_before_revision && <button className="btn" disabled={blocked} onClick={() => void older('dependencies')}>Ältere Verknüpfungen</button>}
      </section>}
    </>}
  </div>;
}
