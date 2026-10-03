import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { correctionFollowupsApi, type CorrectionImpact, type FollowupCommand, type FollowupStatus } from '../../api/allocationAdoptions';
import { CorrectionImpactList } from './AllocationCorrectionPreview';
import { correctionDestination } from './correctionLinks';

type Item = { id: string; snapshot: CorrectionImpact };
type Pending = { item: Item; body: FollowupCommand; key: string };
const blockers: Record<string, string> = {
  'Original financial context could not be proven': 'Der ursprüngliche Abrechnungskontext ist nicht vollständig nachgewiesen. Eine gesonderte Prüfung ist erforderlich.',
  'Financial successor context changed': 'Der Nachfolger gehört nicht zum nachgewiesenen ursprünglichen Abrechnungskontext.',
  'Finalized financial predecessor required': 'Die Nachfolgerkette benötigt einen festgeschriebenen Vorgänger.',
  'Finalize the financial correction successor': 'Der finanzielle Nachfolger muss vollständig geprüft und festgeschrieben werden.',
  'Recompute with current inputs after the allocation correction': 'Eingaben und Vollständigkeit nach der Korrektur prüfen und den Vorgang neu berechnen.',
  'Refresh financial computation and coverage': 'Berechnung und Vollständigkeitsnachweis erneuern.',
  'Financial computation is no longer current': 'Die gespeicherte Berechnung passt nicht mehr zu den aktuellen Eingaben. Erneut prüfen und berechnen.',
  'Review the current correction source in the consumed coverage declaration': 'Die aktuelle Belegversion in den Vollständigkeitsnachweis aufnehmen, prüfen und neu berechnen.',
};

function Followup({ item, pending, setPending, parentBusy, generation }: {
  item: Item; pending?: Pending; setPending: (value: Pending | null) => void; parentBusy: boolean; generation: number;
}) {
  const [status, setStatus] = useState<FollowupStatus | null>(null);
  const [loading, setLoading] = useState(false); const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null); const [reason, setReason] = useState('');
  const [saved, setSaved] = useState(false);
  const [sourceReviewed, setSourceReviewed] = useState(false); const [confirmed, setConfirmed] = useState(false);
  const request = useRef<AbortController | null>(null); const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort(); }; }, []);
  useEffect(() => {
    request.current?.abort(); setStatus(null); setLoading(false); setSourceReviewed(false); setConfirmed(false);
  }, [generation, parentBusy]);
  async function load() {
    if (parentBusy || sending) return;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setLoading(true); setStatus(null); setError(null); setSaved(false); setSourceReviewed(false); setConfirmed(false);
    try {
      const result = await correctionFollowupsApi.status(item.id, controller.signal);
      if (!controller.signal.aborted) setStatus(result);
    } catch {
      if (!controller.signal.aborted) setError('Folgearbeit derzeit nicht vollständig prüfbar. Berechtigungen prüfen und erneut laden.');
    } finally { if (!controller.signal.aborted) setLoading(false); }
  }
  async function send() {
    if (parentBusy || sending || loading) return;
    let command = pending;
    if (!command) {
      if (!status?.ready || !status.preview_digest || !reason.trim() || !confirmed || status.source_review_required && !sourceReviewed) return;
      command = { item, key: crypto.randomUUID(), body: { preview_digest: status.preview_digest, reason: reason.trim(), source_reviewed: sourceReviewed, confirmed: true } };
      setPending(command);
    }
    setSending(true); setError(null); setSaved(false);
    try {
      await correctionFollowupsApi.complete(command.item.id, command.body, command.key);
      if (!alive.current) return;
      setPending(null); setStatus(null); setConfirmed(false); setSourceReviewed(false); setSaved(true);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        setPending(null); setStatus(null); setConfirmed(false);
        setError('Abschluss nicht bestätigt. Aktuelle Folgearbeit neu laden und prüfen.');
      } else setError('Keine bestätigte Antwort erhalten. Denselben Abschluss erneut senden, um seinen Ausgang festzustellen.');
    } finally { if (alive.current) setSending(false); }
  }
  const disabled = parentBusy || loading || sending || !!pending;
  return <li className="min-w-0 space-y-3 rounded-md border border-input p-3">
    <CorrectionImpactList items={[item.snapshot]} />
    <button className="btn" disabled={parentBusy || loading || sending} onClick={() => void load()}>Bearbeitungsstand prüfen</button>
    {loading && <p role="status">Prüfe aktuelle Berechnung und Nachweise…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {saved && <p role="status">Abschlussnachweis gespeichert. Den aktuellen Bearbeitungsstand können Sie erneut prüfen.</p>}
    {pending && <button className="btn" disabled={parentBusy || loading || sending} onClick={() => void send()}>Denselben Abschluss erneut senden</button>}
    {sending && <p role="status">Bestätige Folgearbeit…</p>}
    {!parentBusy && status && <>
      <p role="status">{status.status === 'completed' ? 'Folgearbeit nachweisbar erledigt.' : status.status === 'changed' ? 'Nach dem letzten Abschluss hat sich die Grundlage geändert. Erneute Prüfung erforderlich.' : 'Folgearbeit offen.'}</p>
      {status.blocker && <p>{blockers[status.blocker] ?? 'Vollständige aktuelle Prüfung erforderlich.'}</p>}
      {status.target_id && <a className="block underline" href={correctionDestination({ ...item.snapshot, id: status.target_id })}>Geprüften Finanzvorgang öffnen</a>}
      {status.source_document_id && <a className="block underline" href={`/belege/${status.source_document_id}`}>Aktuelle Belegversion öffnen</a>}
      {status.ready && status.status !== 'completed' && <fieldset disabled={disabled} className="min-w-0 space-y-3">
        <legend>Folgearbeit bestätigen</legend>
        <p>Der Vorgang wurde mit den aktuellen Eingaben berechnet. Manuelle Angaben und fachliche Folgen müssen vollständig geprüft sein.</p>
        <label className="block">Begründung des Abschlusses<textarea className="block w-full min-w-0 rounded-md border border-input bg-background px-3 py-2" maxLength={2000} value={reason} onChange={event => { setReason(event.target.value); setConfirmed(false); }} /></label>
        {status.source_review_required && <label className="flex items-start gap-2"><input type="checkbox" checked={sourceReviewed} onChange={event => setSourceReviewed(event.target.checked)} />Aktuelle Belegversion und ihren Nachweis in der verwendeten Deckungserklärung geprüft.</label>}
        <label className="flex items-start gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />Aktuelle Berechnung, manuelle Eingaben und erforderlichen Abschluss geprüft.</label>
        <button className="btn" disabled={disabled || !reason.trim() || !confirmed || status.source_review_required && !sourceReviewed} onClick={() => void send()}>Folgearbeit verbindlich abschließen</button>
      </fieldset>}
      {status.history.length > 0 && <details><summary>Bisherige Abschlussnachweise ({status.history.length})</summary><ol className="space-y-2">{status.history.map(receipt => <li key={receipt.id} className="break-words">{new Date(receipt.completed_at).toLocaleString('de-DE')} · {receipt.reason}</li>)}</ol></details>}
    </>}
  </li>;
}

export function CorrectionFollowups({ items, parentBusy, generation }: { items: Item[]; parentBusy: boolean; generation: number }) {
  // Keep unknown commands even when the current correction head changes or a
  // parent GET temporarily loses its receipt. A real denial remounts the parent.
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const retained = new Map(items.map(item => [item.id, item]));
  for (const value of Object.values(pending)) retained.set(value.item.id, value.item);
  return <section hidden={parentBusy || retained.size === 0} aria-label="Bearbeitung der Korrekturfolgen" className="space-y-3">
    <h4>Folgearbeiten</h4>
    <ul className="space-y-4">{[...retained.values()].map(item => <Followup key={item.id} item={item} pending={pending[item.id]} parentBusy={parentBusy} generation={generation}
      setPending={value => setPending(previous => { const next = { ...previous }; if (value) next[item.id] = value; else delete next[item.id]; return next; })} />)}</ul>
  </section>;
}
