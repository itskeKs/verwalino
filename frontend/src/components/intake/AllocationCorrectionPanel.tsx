import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { allocationCorrectionsApi, type CorrectionCommand, type CorrectionPreview, type CorrectionResult } from '../../api/allocationAdoptions';
import { AllocationCorrectionPreview } from './AllocationCorrectionPreview';
import { CorrectionFollowups } from './CorrectionFollowups';

function Confirmation({ preview, reviewId, disabled, submit }: { preview: CorrectionPreview; reviewId: string; disabled: boolean; submit: (body: CorrectionCommand) => void }) {
  const [reason, setReason] = useState('');
  const [costs, setCosts] = useState(false); const [disclosure, setDisclosure] = useState(false); const [followups, setFollowups] = useState(false);
  return <div className="space-y-3">
    <p>Die Bestätigung ersetzt alle bisherigen Teilkosten gemeinsam. Frühere Kosten und Belegversionen bleiben erhalten. Betroffene Finanzvorgänge müssen anschließend gesondert bearbeitet werden.</p>
    <fieldset className="min-w-0 space-y-3" disabled={disabled}>
      <label className="block">Begründung der Korrektur<textarea className="block w-full min-w-0 rounded-md border border-input bg-background px-3 py-2" maxLength={2000} value={reason} onChange={event => { setReason(event.target.value); setCosts(false); setDisclosure(false); setFollowups(false); }} /></label>
      <label className="flex items-start gap-2"><input type="checkbox" checked={costs} onChange={event => setCosts(event.target.checked)} />Alle neuen Teilkosten, Beträge und Verteilungen geprüft; gemeinsam ersetzen.</label>
      <label className="flex items-start gap-2"><input type="checkbox" checked={disclosure} onChange={event => setDisclosure(event.target.checked)} />Vollständiges Original geprüft; neue Belegversion im Abrechnungskreis freigeben.</label>
      <label className="flex items-start gap-2"><input type="checkbox" checked={followups} onChange={event => setFollowups(event.target.checked)} />Alle angezeigten Folgen geprüft; erforderliche Folgearbeiten bleiben offen.</label>
    </fieldset>
    <button className="btn" disabled={disabled || !reason.trim() || !costs || !disclosure || !followups || !preview.preview_digest} onClick={() => submit({ treatment_review_id: reviewId, preview_digest: preview.preview_digest!, reason: reason.trim(), costs_confirmed: true, whole_original_disclosure_reviewed: true, downstream_followups_reviewed: true })}>Gesamte Aufteilung verbindlich korrigieren</button>
  </div>;
}

export function AllocationCorrectionPanel({ allocationId, reviewId, parentBusy, parentGeneration, receipt, onConfirmed }: {
  allocationId: string | null; reviewId: string | null; parentBusy: boolean; parentGeneration: number;
  receipt: CorrectionResult | null; onConfirmed: () => void;
}) {
  const [sending, setSending] = useState(false); const [unknown, setUnknown] = useState(false);
  const [error, setError] = useState<string | null>(null); const [generation, setGeneration] = useState(0);
  const pending = useRef<{ predecessor: string; body: CorrectionCommand; key: string } | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  async function send(body?: CorrectionCommand) {
    if (sending || parentBusy) return;
    if (!pending.current) {
      if (!body || !allocationId) return;
      pending.current = { predecessor: allocationId, body, key: crypto.randomUUID() };
    }
    const request = pending.current; setSending(true); setError(null);
    try {
      await allocationCorrectionsApi.create(request.predecessor, request.body, request.key);
      if (!alive.current) return;
      pending.current = null; setUnknown(false); onConfirmed();
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setUnknown(false);
        setError(failure.status === 409 ? 'Die Korrekturgrundlage hat sich geändert. Vorschau neu laden und vollständig prüfen.' : 'Korrektur nicht bestätigt. Bitte Angaben und Berechtigungen prüfen.');
        onConfirmed();
      } else {
        setUnknown(true); setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Korrektur erneut, um ihren Ausgang festzustellen.');
      }
    } finally { if (alive.current) { setSending(false); setGeneration(value => value + 1); } }
  }
  return <div className="min-w-0 space-y-4">
    {!parentBusy && receipt && <section className="space-y-3" aria-label="Bestätigte Korrektur">
      <h3>Aufteilung korrigiert</h3><p>{new Date(receipt.receipt.confirmed_at).toLocaleString('de-DE')} · {receipt.allocation.cost_count} neue Teilkosten</p>
      <p className="break-words">{receipt.receipt.reason}</p>
      <a className="block underline" href={`/belege/${receipt.allocation.source_document_id}`}>Freigegebene Belegversion der Korrektur öffnen</a>
      {receipt.receipt.impacts.length ? <p>Prüfen Sie den aktuellen Bearbeitungsstand der betroffenen Finanzvorgänge.</p> : <p>Für diese Korrektur wurden keine betroffenen Finanzvorgänge festgestellt.</p>}
    </section>}
    <CorrectionFollowups items={receipt?.receipt.impacts ?? []} parentBusy={parentBusy} generation={parentGeneration} />
    {error && <p role="alert" className="error-text">{error}</p>}
    {sending && <p role="status">Bestätige vollständige Korrektur…</p>}
    {unknown && <button className="btn" disabled={sending || parentBusy} onClick={() => void send()}>Dieselbe Korrektur erneut senden</button>}
    {allocationId && <AllocationCorrectionPreview allocationId={allocationId} reviewId={reviewId} parentBusy={parentBusy || sending || unknown} parentGeneration={parentGeneration + generation}
      confirmation={preview => reviewId && <Confirmation key={preview.preview_digest} preview={preview} reviewId={reviewId} disabled={parentBusy || sending || unknown} submit={body => void send(body)} />} />}
  </div>;
}
