import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { allocationReplacementsApi, type CorrectionCommand, type ReplacementCandidate } from '../../api/allocationAdoptions';
import { AllocationCorrectionPreview } from './AllocationCorrectionPreview';
import { Confirmation } from './AllocationCorrectionPanel';

const denied = (failure: unknown) => failure instanceof ApiError && [401, 403, 404].includes(failure.status);
export function AllocationReplacementPanel({ reviewId, enabled, parentBusy, generation, onPending, onConfirmed }: {
  reviewId: string | null; enabled: boolean; parentBusy: boolean; generation: number; onPending: (value: boolean) => void; onConfirmed: () => void;
}) {
  const [items, setItems] = useState<ReplacementCandidate[]>([]); const [after, setAfter] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReplacementCandidate | null>(null); const [busy, setBusy] = useState(false); const [sending, setSending] = useState(false);
  const [unknown, setUnknown] = useState(false); const [error, setError] = useState<string | null>(null); const [loaded, setLoaded] = useState(false);
  const [revision, setRevision] = useState(0);
  const pending = useRef<{ predecessor: string; body: CorrectionCommand; key: string } | null>(null);
  const epoch = useRef(0); const read = useRef<AbortController | null>(null); const alive = useRef(true);
  const onPendingRef = useRef(onPending);
  useEffect(() => { onPendingRef.current = onPending; }, [onPending]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; read.current?.abort(); onPendingRef.current(false); }; }, []);
  useEffect(() => {
    read.current?.abort(); setItems([]); setAfter(null); setLoaded(false); setBusy(false);
    if (!pending.current) { setSelected(null); return; }
    // Recheck access to the immutable command after context changes. A denial
    // invalidates even a later response from an already in-flight POST.
    const controller = new AbortController(); read.current = controller;
    const request = pending.current; setBusy(true);
    allocationReplacementsApi.preview(request.predecessor, request.body.treatment_review_id, controller.signal).catch(failure => {
      if (!controller.signal.aborted && denied(failure)) {
        epoch.current++; pending.current = null; setUnknown(false); setSelected(null); onPendingRef.current(false);
        setError('Berechtigung für diese Ersetzung nicht mehr vorhanden.');
      }
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [reviewId, generation]);
  async function load(more = false) {
    if (!reviewId || !enabled || busy || sending || unknown || parentBusy) return;
    read.current?.abort(); const controller = new AbortController(); read.current = controller; setBusy(true); setError(null);
    try {
      const page = await allocationReplacementsApi.candidates(reviewId, more ? after ?? undefined : undefined, controller.signal);
      if (controller.signal.aborted) return;
      setItems(previous => more ? [...previous, ...page.items] : page.items); setAfter(page.next_after); setLoaded(true); setSelected(null);
    } catch { if (!controller.signal.aborted) { setItems([]); setSelected(null); setError('Bisherige Belege derzeit nicht verfügbar.'); } }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  async function send(body?: CorrectionCommand) {
    if (sending || busy || parentBusy) return;
    if (!pending.current) {
      if (!body || !selected || !enabled) return;
      pending.current = { predecessor: selected.id, body, key: crypto.randomUUID() };
    }
    const request = pending.current; const currentEpoch = epoch.current; setSending(true); setError(null); onPending(true);
    try {
      await allocationReplacementsApi.create(request.predecessor, request.body, request.key);
      if (!alive.current || epoch.current !== currentEpoch) return;
      pending.current = null; setUnknown(false); onPending(false); setSelected(null); onConfirmed();
    } catch (failure) {
      if (!alive.current || epoch.current !== currentEpoch) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setUnknown(false); onPending(false); setSelected(null); setRevision(value => value + 1);
        setError('Ersetzung nicht bestätigt. Bisherigen Beleg erneut auswählen und aktuelle Vorschau prüfen.'); onConfirmed();
      } else { setUnknown(true); setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Belegersetzung erneut, um ihren Ausgang festzustellen.'); }
    } finally { if (alive.current) setSending(false); }
  }
  if (!enabled && !unknown && !sending) return null;
  return <section className="min-w-0 space-y-3 border-t border-input pt-4" aria-label="Geänderten Beleg ersetzen">
    <h3>Geänderten Beleg als Ersatz übernehmen</h3>
    <p>Für bereits bestätigte Kosten: Wählen Sie den bisherigen Beleg desselben Abrechnungskreises. Beide Originale und alle früheren Kosten bleiben erhalten.</p>
    <button className="btn" disabled={!enabled || !reviewId || busy || sending || unknown || parentBusy} onClick={() => void load()}>Bisherigen Beleg suchen</button>
    {busy && <p role="status">Prüfe zugängliche bisherige Belege…</p>}{sending && <p role="status">Bestätige Belegersetzung…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {unknown && <button className="btn" disabled={busy || sending || parentBusy} onClick={() => void send()}>Dieselbe Belegersetzung erneut senden</button>}
    {!unknown && !sending && loaded && items.length === 0 && <p>Keine zugänglichen bestätigten Belege mit diesem Lieferanten und dieser Rechnungsnummer gefunden.</p>}
    {!!items.length && !unknown && <label className="block">Bisheriger Beleg<select className="block w-full min-w-0" value={selected?.id ?? ''} disabled={busy || sending || parentBusy} onChange={event => { setSelected(items.find(item => item.id === event.target.value) ?? null); setError(null); }}>
      <option value="">Beleg auswählen</option>{items.map(item => <option key={item.id} value={item.id}>{item.seller_name} · {item.document_number} · {item.gross_amount} EUR · {new Date(item.confirmed_at).toLocaleString('de-DE')}</option>)}
    </select></label>}
    {after && !unknown && <button className="btn" disabled={busy || sending || parentBusy} onClick={() => void load(true)}>Weitere bisherige Belege laden</button>}
    {selected && !unknown && <><a className="block underline" href={`/belege/${selected.source_document_id}`} target="_blank" rel="noreferrer">Bisheriges aufbewahrtes Original öffnen</a>
      <AllocationCorrectionPreview replacement onDenied={() => {
        epoch.current++; pending.current = null; setUnknown(false); setSelected(null); setItems([]); setAfter(null); setLoaded(false); onPending(false);
        setError('Berechtigung für den bisherigen Beleg nicht mehr vorhanden.');
      }} allocationId={selected.id} reviewId={reviewId} parentBusy={busy || sending || parentBusy} parentGeneration={generation + revision}
        confirmation={preview => reviewId && <Confirmation replacement key={preview.preview_digest} preview={preview} reviewId={reviewId} disabled={busy || sending || parentBusy}
          submit={body => { if ('whole_original_disclosure_reviewed' in body) void send(body); }} />} /></>}
  </section>;
}
