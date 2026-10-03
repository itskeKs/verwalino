import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api, ApiError } from '../../api/client';
import { exceptionApi, type CompletionCommand, type CompletionPreview, type CompletionReceipt, type ExceptionCase } from '../../api/exceptions';

type Draft = { preview: CompletionPreview; reason: string; acknowledged: boolean };
const fresh = (preview: CompletionPreview): Draft => ({ preview, reason: '', acknowledged: false });
const denied = (error: unknown) => error instanceof ApiError && [401, 403, 404].includes(error.status);
const dispositions: Record<string, string> = {
  fulfilled_by_source_replacement: 'Durch bestätigte Belegersetzung erfüllt',
  fulfilled_by_adoption: 'Durch bestätigte Kostenübernahme erfüllt', superseded_by_adopted_context: 'Durch den übernommenen, neueren Kontext ersetzt',
  fulfilled_by_current_comparison: 'Durch aktuellen Belegvergleich erfüllt', superseded_by_current_comparison: 'Durch aktuellen Belegvergleich ersetzt',
};
function Evidence({ evidence }: { evidence: Pick<CompletionPreview, 'members' | 'comparisons' | 'dispositions'> }) {
  const label = (id: string) => { const index = evidence.members.findIndex(member => member.logical_document_id === id); return index < 0 ? 'Beleg' : `Beleg ${index + 1}`; };
  return <div className="min-w-0 space-y-4">
    <h3>Alle zusammenhängenden Belege</h3>
    <ol className="space-y-3">{evidence.members.map((member, index) => <li className="min-w-0 space-y-1" key={member.logical_document_id}>
      <p className="break-words">Beleg {index + 1}{member.document_number ? ` · ${member.document_number}` : ''}{member.gross_amount ? ` · ${member.gross_amount} EUR` : ''}</p>
      <p>{member.allocation_set_id ? member.disposition === 'replaced_document' ? 'Bisheriger Beleg und alle Teilkosten wurden durch eine bestätigte Belegrevision ersetzt.' : member.disposition === 'adopted_document' ? `${member.cost_count} bestätigte Teilkosten` : 'Kopie desselben Belegs; durch die bestehende Kostenübernahme abgedeckt' : 'Bestätigte Kostenübernahme fehlt'}</p>
      <Link className="block underline" to={`/posteingang/belege/${member.document_ref_id}?logical_document_id=${encodeURIComponent(member.logical_document_id)}`}>Beleg {index + 1} prüfen</Link>
      {member.source_document_id && <Link className="block underline" to={`/belege/${member.source_document_id}`}>Freigegebenes Original für Beleg {index + 1}</Link>}
      {member.replacement && <p><Link className="underline" to={`/belege/${member.replacement.source_document_id}`}>Bestätigten Ersatz für Beleg {index + 1} öffnen</Link> · {member.replacement.cost_count} neue Teilkosten{member.replacement.gross_amount ? ` · ${member.replacement.gross_amount} EUR` : ''}</p>}
      {member.primary_cost_id && <Link className="block underline" to={`/kosten/${member.primary_cost_id}`}>Kostenübernahme für Beleg {index + 1}</Link>}
    </li>)}</ol>
    {evidence.comparisons.length > 0 && <><h3>Erforderliche Belegvergleiche</h3><ul className="space-y-2">{evidence.comparisons.map(pair => <li key={pair.left_logical_id + pair.right_logical_id}>
      {label(pair.left_logical_id)} und {label(pair.right_logical_id)}: {pair.verdict === 'confirmed_replacement' ? 'Durch bestätigte Belegersetzung verbunden' : pair.verdict === 'same_document' ? 'Derselbe Beleg' : pair.verdict === 'distinct_documents' ? 'Verschiedene Belege' : 'Noch nicht abschließend geklärt'}
    </li>)}</ul></>}
    <h3>Erhaltene Fallhinweise</h3><ul className="space-y-2">{evidence.dispositions.map((item, index) => <li key={item.dependency_id}>
      Hinweis {index + 1} ({item.kind === 'context' ? 'Zuordnung' : 'Dublette'}): {dispositions[item.disposition] ?? 'Noch zu prüfen'}
    </li>)}</ul>
  </div>;
}

export function ExceptionCompletionPanel({ caseId, item, parentBusy, parentGeneration, selectedReceiptId, onCompleted }: {
  caseId: string; item: ExceptionCase | null; parentBusy: boolean; parentGeneration: number; selectedReceiptId?: string | null; onCompleted: () => void;
}) {
  const [currentCase, setCurrentCase] = useState<ExceptionCase | null>(null);
  const [retainedAuthorized, setRetainedAuthorized] = useState(false);
  const [preview, setPreview] = useState<CompletionPreview | null>(null); const [receipt, setReceipt] = useState<CompletionReceipt | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null); const [busy, setBusy] = useState(true); const [sending, setSending] = useState(false);
  const [generation, setGeneration] = useState(0); const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ body: CompletionCommand; expected: string; key: string } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const receiptId = selectedReceiptId ?? item?.completion_id;
  const retainedReviews = draft?.preview.members.map(member => member.invoice_review_id).join(',') ?? '';
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setPreview(null); setReceipt(null); setCurrentCase(null); setRetainedAuthorized(false);
    async function load() {
      const [current, effective] = await Promise.all([
        exceptionApi.completionPreview(caseId, controller.signal), exceptionApi.get(caseId, controller.signal),
        ...retainedReviews.split(',').filter(Boolean).map(review => api.get(`/api/invoice-reviews/${review}`, { signal: controller.signal })),
      ]);
      const selected = selectedReceiptId ?? effective.completion_id;
      const recorded = selected ? await exceptionApi.completion(caseId, selected, controller.signal) : null;
      if (controller.signal.aborted) return;
      setPreview(current); setReceipt(recorded); setCurrentCase(effective); setRetainedAuthorized(true);
      if (!current.preview_digest && !pending.current) setDraft(null);
      else if (current.preview_digest) setDraft(previous => previous ?? fresh(current));
    }
    load().catch(failure => {
      if (controller.signal.aborted) return;
      if (denied(failure)) { setDraft(null); pending.current = null; setSubmitted(false); }
      setError('Abschlussprüfung derzeit nicht verfügbar. Bitte erneut laden.');
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [caseId, receiptId, selectedReceiptId, retainedReviews, generation, parentGeneration]);
  const blocked = busy || parentBusy || sending;
  const eligible = !!preview?.can_complete && !!currentCase?.can_work && currentCase.workflow_state !== 'completed';
  const stale = conflict || !!draft && (draft.preview.preview_digest !== preview?.preview_digest || draft.preview.expected_event_id !== preview?.expected_event_id);
  async function save() {
    if (blocked || !currentCase?.can_work || !retainedAuthorized || !draft || (!pending.current && (!eligible || !draft.preview.preview_digest || stale || !draft.acknowledged || !draft.reason.trim()))) return;
    if (!pending.current) pending.current = { key: crypto.randomUUID(), expected: draft.preview.expected_event_id,
      body: { preview_digest: draft.preview.preview_digest!, reason: draft.reason.trim(), completion_reviewed: true } };
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      await exceptionApi.complete(caseId, request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setConflict(false); onCompleted(); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if (denied(failure)) { setPreview(null); setReceipt(null); setDraft(null); }
        if (failure.status === 409) setConflict(true);
        setError(failure.status === 409 ? 'Die Abschlussgrundlage hat sich geändert. Prüfen Sie den aktuellen Stand.' : 'Abschluss nicht bestätigt. Bitte Angaben und Berechtigung prüfen.');
        setGeneration(value => value + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie denselben Abschluss erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="card min-w-0 space-y-4" aria-label="Fall abschließen">
    <div className="flex flex-wrap justify-between gap-3"><h2>Fall abschließen</h2><button className="btn" disabled={blocked} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Abschlussprüfung aktualisieren</button></div>
    <p>Prüfen Sie alle zusammenhängenden Belege, Vergleiche und erhaltenen Fallhinweise. Der Abschluss dokumentiert die bereits bestätigten Ergebnisse.</p>
    {busy && <p role="status">Prüfe Abschlussgrundlage…</p>}{error && <p role="alert" className="error-text">{error}</p>}
    {item && currentCase && !parentBusy && preview && <>
      {receipt && <details className="space-y-3"><summary>Gespeicherter Abschluss vom {new Date(receipt.created_at).toLocaleString('de-DE')}</summary>
        <p>{receipt.id !== currentCase.completion_id ? 'Historischer Abschlussbeleg; maßgeblich ist der aktuelle Fallstand.' : currentCase.completion_changed ? 'Die Grundlage hat sich geändert. Der Fall ist wieder offen.' : 'Der gespeicherte Abschluss gilt für den aktuellen Stand.'}</p><p className="break-words">{receipt.reason}</p><Evidence evidence={receipt} /></details>}
      {currentCase.completion_changed && <p role="status">Neue oder geänderte Belege erfordern eine erneute Prüfung. Der bisherige Abschluss bleibt erhalten.</p>}
      {!eligible && currentCase.workflow_state !== 'completed' && <p>Abschluss derzeit nicht möglich. Vollständige aktuelle Belege, Vergleiche, bestätigte Kostenübernahmen und Berechtigungen sind erforderlich.</p>}
      <Evidence evidence={preview} />
      {draft && retainedAuthorized && (eligible || submitted) && <>
        {stale && !submitted && <div><p>Prüfen Sie die geänderte Grundlage und bestätigen Sie sie erneut.</p><button className="btn" disabled={blocked || !eligible} onClick={() => { setDraft(fresh(preview)); setConflict(false); setError(null); }}>Aktuelle Abschlussgrundlage übernehmen</button></div>}
        {submitted && <p>Der gesendete Auftrag bleibt unverändert. Eine Wiederholung liefert seinen Beleg; der aktuelle Fallstand wird anschließend neu geprüft.</p>}
        <fieldset className="min-w-0 space-y-3" disabled={blocked || submitted || stale || !eligible}>
          <label className="block" htmlFor="completion-reason">Begründung des Abschlusses</label><textarea id="completion-reason" className="w-full rounded-md border border-input bg-background px-3 py-2" maxLength={1000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value, acknowledged: false })} />
          <label className="flex items-start gap-2"><input type="checkbox" checked={draft.acknowledged} onChange={event => setDraft({ ...draft, acknowledged: event.target.checked })} />Alle Belege, Vergleiche und Fallhinweise geprüft; bestätigte Ergebnisse als Abschluss anerkennen.</label>
        </fieldset>
        <button className="btn" disabled={blocked || !currentCase.can_work || (!submitted && (!eligible || !draft.preview.preview_digest || stale || !draft.acknowledged || !draft.reason.trim()))} onClick={() => void save()}>{submitted ? 'Denselben Abschluss erneut senden' : 'Geprüften Fall abschließen'}</button>
      </>}
    </>}
  </section>;
}
