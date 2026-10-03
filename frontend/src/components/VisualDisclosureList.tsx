import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import { visualDisclosureApi, type VisualDisclosure } from '../api/visualDisclosures';

type Props = { endpoint: string; onWithdrawn?: () => void; refreshGeneration?: number; enabled?: boolean; mutationDisabled?: boolean; onPendingChange?: (pending: boolean) => void };
type Withdrawal = { target: string; reason: string; key: string };
export function VisualDisclosureList({ endpoint, refreshGeneration = 0, enabled = true, mutationDisabled = false, onPendingChange, onWithdrawn }: Props) {
  const [items, setItems] = useState<VisualDisclosure[]>([]);
  const [cursor, setCursor] = useState<string | null>(null), [next, setNext] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0), [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<VisualDisclosure | null>(null);
  const [reason, setReason] = useState(''), [ack, setAck] = useState(false);
  const [pending, setPending] = useState<Withdrawal | null>(null), [sending, setSending] = useState(false);
  const [receipt, setReceipt] = useState<VisualDisclosure | null>(null), [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { onPendingChange?.(pending !== null); }, [pending, onPendingChange]);
  useEffect(() => {
    if (!enabled) { setItems([]); setNext(null); setReceipt(null); setLoading(false); return; }
    const controller = new AbortController(); setLoading(true); setItems([]); setNext(null); setReceipt(null);
    visualDisclosureApi.list(endpoint, cursor, controller.signal).then(value => {
      if (!controller.signal.aborted) { setItems(value.items); setNext(value.next_cursor); }
    }).catch(failure => {
      if (!controller.signal.aborted) {
        setError('Freigaben derzeit nicht verfügbar. Bitte erneut laden.');
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) { setSelected(null); setPending(null); }
      }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [endpoint, cursor, generation, refreshGeneration, enabled]);
  async function withdraw() {
    if (!enabled || mutationDisabled || sending || (!pending && (!selected?.current.can_withdraw || !reason.trim() || !ack))) return;
    const command = pending ?? { target: selected!.current.id, reason: reason.trim(), key: crypto.randomUUID() };
    setPending(command); setSending(true); setError(null); setReceipt(null);
    try {
      const result = await visualDisclosureApi.withdraw(command.target, command.reason, command.key);
      if (!alive.current) return;
      setPending(null); setSelected(null); setReason(''); setAck(false); setReceipt(result);
      setItems(previous => previous.filter(item => item.current.id !== command.target));
      onWithdrawn?.();
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        setPending(null); setSelected(null); setItems([]); setError('Widerruf nicht bestätigt. Bitte den aktuellen Freigabestand erneut laden.');
      } else setError('Keine bestätigte Antwort erhalten. Denselben Widerruf erneut senden.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="space-y-3" aria-label="Freigabeverlauf">
    <button className="btn" disabled={loading || sending} onClick={() => { setCursor(null); setError(null); if (!pending) setSelected(null); setGeneration(value => value + 1); }}>Freigaben neu laden</button>
    {error && <p role="alert" className="error-text">{error}</p>}
    {loading ? <p role="status">Lade Freigaben…</p> : items.length === 0 ? <p>Keine Freigaben auf dieser Ergebnisseite.</p> : <ul className="space-y-3">{items.map(item => <li key={item.receipt.id} className="rounded-md border p-3 space-y-2 break-words">
      <p>{item.destination ? `${item.destination.label} · ${item.destination.scope_type}` : 'Zielbereich derzeit nicht lesbar'} · {item.receipt.output_page_count} Seite(n)</p>
      <p>{new Date(item.receipt.created_at).toLocaleString('de-DE')} · {item.current.available ? 'Freigegeben' : 'Widerrufen'}</p>
      {item.current.can_download && <a className="btn" href={visualDisclosureApi.content(item.receipt.id)} target="_blank" rel="noreferrer">Freigegebenen Auszug öffnen</a>}
      {item.current.available && !item.current.can_download && <p>Zum Öffnen ist die Exportberechtigung im Zielbereich erforderlich.</p>}
      {item.current.can_withdraw && <button className="btn" disabled={!!pending || sending || mutationDisabled} onClick={() => { setSelected(item); setReason(''); setAck(false); setReceipt(null); }}>Freigabe widerrufen</button>}
    </li>)}</ul>}
    {next && <button className="btn" disabled={loading || sending || !!pending} onClick={() => { setSelected(null); setCursor(next); }}>Weitere Freigaben</button>}
    {(selected || pending) && <div className="rounded-md border p-3 space-y-3" role="group" aria-label="Widerruf prüfen">
      <p>Der Widerruf sperrt den weiteren Abruf dieser Freigabe. Bereits heruntergeladene Dateien bleiben bei ihren Empfängern.</p>
      <fieldset disabled={sending || !!pending} className="space-y-3">
        <label className="block">Grund des Widerrufs<input className="w-full rounded-md border p-2" maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} /></label>
        <label className="flex gap-2"><input type="checkbox" checked={ack} onChange={event => setAck(event.target.checked)} />Weitere Abrufe dieser Freigabe ausdrücklich sperren.</label>
      </fieldset>
      <button className="btn" disabled={sending || !enabled || mutationDisabled || (!pending && (!ack || !reason.trim()))} onClick={() => void withdraw()}>{pending ? 'Denselben Widerruf erneut senden' : 'Widerruf bestätigen'}</button>
      {!pending && <button className="btn" disabled={sending} onClick={() => setSelected(null)}>Abbrechen</button>}
    </div>}
    {receipt && <p role="status">Widerruf gespeichert. {receipt.current.kind === 'withdrawn' ? 'Diese Freigabe ist derzeit gesperrt.' : 'Inzwischen besteht ein neuerer Freigabestand. Bitte erneut laden.'}</p>}
  </section>;
}
