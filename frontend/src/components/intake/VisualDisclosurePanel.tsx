import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../../api/client';
import { visualExtractApi, type VisualExtract } from '../../api/visualExtracts';
import { visualDisclosureApi, type DisclosurePublish, type DisclosureScope, type DisclosureTarget, type VisualDisclosure } from '../../api/visualDisclosures';
import { SearchChoice } from '../SearchChoice';
import { VisualDisclosureList } from '../VisualDisclosureList';

type Pending = { artifact: string; body: DisclosurePublish; expected: string | null; key: string };
export function VisualDisclosurePanel({ artifact, canPublish, canRead, parentGeneration, onPendingChange }: {
  artifact: VisualExtract; canPublish: boolean; canRead: boolean; parentGeneration: number; onPendingChange: (pending: boolean) => void;
}) {
  const [scope, setScope] = useState<DisclosureScope | null>(null), [target, setTarget] = useState<DisclosureTarget | null>(null);
  const [reason, setReason] = useState(''), [opened, setOpened] = useState(false), [ack, setAck] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null), [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false), [generation, setGeneration] = useState(0), [withdrawPending, setWithdrawPending] = useState(false);
  const [publishedGeneration, setPublishedGeneration] = useState(0);
  const [receipt, setReceipt] = useState<VisualDisclosure | null>(null), [error, setError] = useState<string | null>(null);
  const alive = useRef(true), command = useRef<Pending | null>(null);
  useEffect(() => { command.current = pending; }, [pending]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onPendingChange(!!pending || withdrawPending); }, [pending, withdrawPending, onPendingChange]);
  useEffect(() => {
    setTarget(null); setReceipt(null);
    if (!canRead || !scope) return;
    const controller = new AbortController(); setLoading(true);
    visualDisclosureApi.target(artifact.id, scope.id, controller.signal).then(value => {
      if (!controller.signal.aborted) { setTarget(value); if (!command.current) { setReason(''); setAck(false); setOpened(false); } }
    }).catch(failure => {
      if (!controller.signal.aborted) {
        setError('Freigabegrundlage nicht verfügbar. Bitte Quellstand und Rechte im Zielbereich prüfen.');
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
          setTarget(null); setPending(null); setScope(null); setReason(''); setOpened(false); setAck(false);
        }
      }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [artifact.id, scope, canRead, generation, parentGeneration]);
  const alreadyPublished = target?.current?.current.kind === 'published';
  const valid = !!scope && !!target && !alreadyPublished && opened && ack && !!reason.trim();
  async function publish() {
    if (sending || withdrawPending || !canRead || !canPublish || (!pending && (!valid || loading))) return;
    const request = pending ?? { artifact: artifact.id, expected: target!.current?.current.id ?? null, key: crypto.randomUUID(),
      body: { scope_id: scope!.id, partition_revision_id: artifact.partition_revision_id, content_sha256: artifact.content_sha256, reason: reason.trim(), visible_content_reviewed: true } as DisclosurePublish };
    setPending(request); setSending(true); setError(null); setReceipt(null);
    try {
      const result = await visualDisclosureApi.publish(request.artifact, request.body, request.expected, request.key);
      if (!alive.current) return;
      setPending(null); setReason(''); setAck(false); setOpened(false); setTarget(null); setReceipt(result);
      setPublishedGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        setPending(null); setTarget(null); setOpened(false); setAck(false); setError('Freigabe nicht bestätigt. Bitte den aktuellen Stand neu prüfen.');
      } else setError('Keine bestätigte Antwort erhalten. Dieselbe Freigabe erneut senden; der aktuelle Abrufstatus wird dabei neu geprüft.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="rounded-md border p-3 space-y-3" aria-label="Auszug gezielt freigeben">
    <h4>Auszug für einen Zielbereich prüfen</h4>
    <p>Geben Sie nur diese erzeugte Datei frei. Prüfen Sie auch sichtbare Namen, Adressen und Beträge auf sämtlichen Seiten für den gewählten Empfängerkreis. Die Freigabe bestätigt keine Kosten.</p>
    <Link className="underline" to="/seitenauszuege">Freigegebene Auszüge und eigene ausgehende Freigaben</Link>
    {canRead ? <>
      <fieldset disabled={sending || !!pending || withdrawPending || !canPublish} className="space-y-3">
        <SearchChoice endpoint="/api/accounting-scopes/page?capability=approve" value={scope} onChange={value => { setScope(value); setError(null); setReason(''); setAck(false); setOpened(false); }} label="Zielbereich der Freigabe" display={item => `${item.label} · ${item.scope_type}`} />
        {scope && target && <a className="underline" href={visualExtractApi.content(artifact.id)} target="_blank" rel="noreferrer" onClick={() => setOpened(true)}>Genau diesen Auszug für den Zielbereich öffnen</a>}
        <label className="block">Begründung der Offenlegung<input className="w-full rounded-md border p-2" maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <label className="flex gap-2"><input type="checkbox" checked={ack} disabled={!opened || !target} onChange={event => setAck(event.target.checked)} />Alle sichtbaren Inhalte dieses Auszugs für den ausgewählten Zielbereich geprüft und zur Offenlegung freigegeben.</label>
      </fieldset>
      {loading && <p role="status">Prüfe aktuellen Freigabestand…</p>}
      {alreadyPublished && <p>Dieser Auszug ist für den gewählten Bereich bereits freigegeben.</p>}
      {!canPublish && <p>Neue Freigaben sind mit den aktuellen Quellrechten nicht möglich. Bestehende Freigaben können Sie unten prüfen.</p>}
      <button className="btn" disabled={sending || withdrawPending || !canPublish || (!pending && (!valid || loading))} onClick={() => void publish()}>{pending ? 'Dieselbe Freigabe erneut senden' : 'Geprüften Auszug freigeben'}</button>
      <button className="btn" disabled={sending || withdrawPending} onClick={() => { setError(null); setGeneration(value => value + 1); }}>Freigabegrundlage neu prüfen</button>
      {error && <p role="alert" className="error-text">{error}</p>}
      {receipt && <p role="status">Freigabebefehl gespeichert. {receipt.current.available ? 'Dieser Auszug ist derzeit freigegeben.' : 'Die gespeicherte Bestätigung ist historisch; diese Freigabe ist derzeit nicht abrufbar.'}</p>}
    </> : <p>Die privaten Auszugsdaten sind derzeit nicht verfügbar. Ausgehende Freigaben können Sie über die Übersicht prüfen und gegebenenfalls widerrufen.</p>}
    <div hidden={!canRead}><VisualDisclosureList endpoint={`/api/visual-extracts/${artifact.id}/disclosures`} enabled={canRead}
      onWithdrawn={() => { setReceipt(null); setGeneration(value => value + 1); }}
      mutationDisabled={!!pending || sending} refreshGeneration={generation + parentGeneration + publishedGeneration} onPendingChange={setWithdrawPending} /></div>
  </section>;
}
