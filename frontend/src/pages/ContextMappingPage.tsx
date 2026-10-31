import { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { contextMappingsApi, type ContextMapping, type MappingStatus } from '../api/contextMappings';

export function ContextMappingPage() {
  const { versionId } = useParams<{ versionId: string }>();
  return versionId ? <MappingDetail key={versionId} versionId={versionId} /> : <p>Zuordnung nicht verfügbar.</p>;
}
function MappingDetail({ versionId }: { versionId: string }) {
  const [view, setView] = useState<MappingStatus | null>(null); const [reason, setReason] = useState(''); const [ack, setAck] = useState(false);
  const [receipt, setReceipt] = useState<ContextMapping | null>(null); const [busy, setBusy] = useState(false); const [sending, setSending] = useState(false);
  const [generation, setGeneration] = useState(0); const [submitted, setSubmitted] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ version: string; reason: string; key: string } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setView(null);
    contextMappingsApi.status(versionId, controller.signal).then(result => { if (!controller.signal.aborted) setView(result); })
      .catch(failure => { if (!controller.signal.aborted) {
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { pending.current = null; setSubmitted(false); setReason(''); setAck(false); setReceipt(null); }
        setError('Zuordnung derzeit nicht verfügbar. Bitte erneut laden.');
      } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [versionId, generation]);
  const blocked = busy || sending;
  async function revoke() {
    if (blocked || !view?.can_prepare || (!pending.current && (view.current.state !== 'active' || !reason.trim() || !ack))) return;
    if (!pending.current) pending.current = { version: view.current.id, reason: reason.trim(), key: crypto.randomUUID() };
    const request = pending.current; setSubmitted(true); setSending(true); setError(null);
    try {
      const result = await contextMappingsApi.revoke(request.version, request.reason, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setReason(''); setAck(false); setReceipt(result); setGeneration(n => n + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false); setAck(false);
        if ([401, 403, 404].includes(failure.status)) { setView(null); setReason(''); setReceipt(null); }
        setError('Widerruf nicht bestätigt. Prüfen Sie den aktuellen Stand erneut.'); setGeneration(n => n + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie denselben Widerruf erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  return <main className="mx-auto max-w-3xl space-y-4 p-4"><div className="flex flex-wrap justify-between gap-3"><h1>Gespeicherte Zuordnung</h1><button className="btn" disabled={blocked} onClick={() => { setAck(false); setError(null); setGeneration(n => n + 1); }}>Zuordnung neu laden</button></div>
    <p>Diese Zuordnung unterstützt die manuelle Prüfung weiterer Belege. Ein Widerruf beendet die künftige Wiederverwendung auch für frühere Leistungszeiträume.</p>
    {busy && <p role="status">Lade aktuellen Zustand…</p>}{error && <p role="alert">{error}</p>}
    {receipt && <p>Widerruf bestätigt, Version {receipt.revision_number}. Maßgeblich für die weitere Verwendung ist der neu geladene aktuelle Zustand.</p>}
    {view && <section className="card min-w-0 space-y-3">
      <h2>Aktueller Zustand: {view.current.state === 'active' ? 'aktiv' : 'widerrufen'}</h2>
      <p>Version {view.current.revision_number}{view.receipt.id !== view.current.id ? ` · geöffneter Nachweis: Version ${view.receipt.revision_number}` : ''}</p>
      <p className="break-words">{view.current.identifier_kind === 'property_id' ? 'Objekt-ID' : 'Zählernummer'}: {view.current.identifier_value}</p>
      <p>Leistungszeitraum: {view.current.valid_from} bis {view.current.valid_to}</p>
      <p><a className="underline" href={`/objekte/${view.current.property_id}`}>Objekt öffnen</a> · <a className="underline" href={`/berechtigungen/teamrechte/${view.current.scope_id}`}>Abrechnungskreis öffnen</a></p>
      {view.current.state === 'active' && !view.master_current && <p>Die aktuellen Stammdaten unterstützen die Wiederverwendung nicht mehr.</p>}
      {view.can_prepare && (view.current.state === 'active' || submitted) && <>
        <fieldset className="space-y-3" disabled={blocked || submitted}>
          <label className="block">Begründung des Widerrufs<textarea className="w-full rounded-md border border-input bg-background p-2" maxLength={2000} value={reason} onChange={e => { setReason(e.target.value); setAck(false); }} /></label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />Wiederverwendung dieser Zuordnung beenden.</label>
        </fieldset>
        <button className="btn btn--wrap" disabled={blocked || (!submitted && (!reason.trim() || !ack))} onClick={() => void revoke()}>{submitted ? 'Denselben Widerruf erneut senden' : 'Zuordnung widerrufen'}</button>
      </>}
    </section>}
  </main>;
}
