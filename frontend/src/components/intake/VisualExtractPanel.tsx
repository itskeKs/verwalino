import { useEffect, useId, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import type { LogicalDocument } from '../../api/intake';
import { visualExtractApi, type ExtractCommand, type VisualExtract } from '../../api/visualExtracts';
import { VisualDisclosurePanel } from './VisualDisclosurePanel';

type Props = { documents: LogicalDocument[]; partitionId: string | null; memberIds: string[];
  canRead: boolean; canGenerate: boolean; disabled: boolean; parentGeneration: number };
type Pending = { logical: string; body: ExtractCommand; key: string };
const denied = (error: unknown) => error instanceof ApiError && [401, 403, 404].includes(error.status);

export function VisualExtractPanel({ documents, partitionId, memberIds, canRead, canGenerate, disabled, parentGeneration }: Props) {
  const selectionId = useId();
  const [logical, setLogical] = useState('');
  const [reason, setReason] = useState('');
  const [pending, setPending] = useState<Pending | null>(null);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<VisualExtract[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<VisualExtract | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);
  const [disclosure, setDisclosure] = useState<VisualExtract | null>(null), [disclosurePending, setDisclosurePending] = useState(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setSaved(null); }, [parentGeneration, canRead]);
  useEffect(() => {
    if (!canRead) { setItems([]); setSaved(null); setNext(null); setLoading(false); return; }
    if (!logical) return;
    const controller = new AbortController();
    setLoading(true); setItems([]); setNext(null);
    visualExtractApi.history(logical, cursor, controller.signal).then(value => {
      if (!controller.signal.aborted) { setItems(value.items); setNext(value.next_cursor); setAccessDenied(false); }
    }).catch(failure => {
      if (!controller.signal.aborted) {
        setError('Gespeicherte Auszüge derzeit nicht verfügbar. Bitte erneut laden.');
        if (denied(failure)) { setSaved(null); setPending(null); setAccessDenied(true); setError('Kein Zugriff auf private Auszüge. Die Leserechte am Original können weiterhin bestehen.'); }
      }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [logical, cursor, generation, canRead, parentGeneration]);
  const selected = documents.find(row => row.id === logical);
  const supported = !!selected && selected.page_start !== null && selected.page_end !== null && selected.page_end - selected.page_start + 1 <= 20;
  const valid = supported && !!partitionId && memberIds.includes(logical) && !!reason.trim();
  const blocked = disabled || sending || disclosurePending;
  async function create() {
    if (blocked || !canRead || accessDenied || !canGenerate || (!pending && !valid)) return;
    const request = pending ?? { logical, body: { partition_revision_id: partitionId!, reason: reason.trim() }, key: crypto.randomUUID() };
    setPending(request); setSending(true); setError(null); setSaved(null);
    try {
      const result = await visualExtractApi.create(request.logical, request.body, request.key);
      if (!alive.current) return;
      setPending(null); setReason(''); setSaved(result); setCursor(null); setGeneration(value => value + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (denied(failure)) { setItems([]); setSaved(null); setPending(null); setAccessDenied(true); setError('Kein Zugriff auf private Auszüge. Bitte Berechtigungen erneut prüfen.'); }
      else if (failure instanceof ApiError && [409, 422].includes(failure.status)) {
        setPending(null); setError('Auszug nicht erzeugt. Bitte aktuelle Aufteilung, Berechtigungen und Quelle prüfen.');
      } else setError('Keine bestätigte Antwort erhalten. Dieselbe Anforderung erneut senden; ein bereits gespeicherter Auszug wird wiedergefunden.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="space-y-3 border-t pt-4" aria-label="Private Seitenauszüge">
    <h3>Privaten Seitenauszug vorbereiten</h3>
    <p>Der Auszug enthält sichtbare Seiten als Bilder. Suchtext, eingebettete Rechnungsdaten und elektronische Signaturen werden nicht übernommen. Der erzeugte Auszug bleibt zunächst privat. Eine Freigabe für einen Zielbereich erfordert eine separate Sichtprüfung.</p>
    <fieldset disabled={blocked || !!pending || !canRead || accessDenied} className="space-y-3">
      <label className="block" htmlFor={selectionId}>Beleg für den Auszug</label>
        <select id={selectionId} className="w-full rounded-md border p-2" value={logical} onChange={event => {
          setLogical(event.target.value); setReason(''); setSaved(null); setDisclosure(null); setError(null); setItems([]); setCursor(null); setNext(null);
        }}><option value="">Beleg auswählen</option>{documents.filter(row => row.page_start !== null).map(row =>
          <option key={row.id} value={row.id}>Seiten {row.page_start}–{row.page_end}{memberIds.includes(row.id) ? ' · aktuelle Aufteilung' : ''}</option>)}</select>
      <label className="block">Grund für den Auszug
        <input className="w-full rounded-md border p-2" maxLength={1000} value={reason} onChange={event => setReason(event.target.value)} />
      </label>
    </fieldset>
    {logical && !memberIds.includes(logical) && <p>Dieser Beleg gehört nicht zur aktuellen geprüften Aufteilung. Vorhandene Auszüge bleiben im Verlauf.</p>}
    {logical && !supported && <p>Ein Auszug kann höchstens 20 PDF-Seiten umfassen.</p>}
    {!canGenerate && <p>Für die Erstellung sind Bearbeitungs- und Exportrechte auf das Original erforderlich.</p>}
    <button className="btn" disabled={blocked || !canRead || accessDenied || !canGenerate || (!pending && !valid)} onClick={() => void create()}>
      {sending ? 'Auszug wird erzeugt…' : pending ? 'Dieselbe Auszugsanforderung erneut senden' : 'Privaten Auszug erzeugen'}</button>
    {error && <p role="alert" className="error-text">{error}</p>}
    {saved && canRead && !accessDenied && <p role="status">Auszug gespeichert. <a className="underline" href={visualExtractApi.content(saved.id)} target="_blank" rel="noreferrer">Erzeugten Auszug öffnen</a></p>}
    {logical && canRead && <div className="space-y-2">
      <div className="flex flex-wrap gap-3"><h4>Gespeicherte Auszüge</h4>
        <button className="btn" disabled={blocked || loading} onClick={() => { setCursor(null); setError(null); setGeneration(value => value + 1); }}>Auszüge neu laden</button></div>
      {loading ? <p role="status">Lade Auszüge…</p> : items.length === 0 || accessDenied ? <p>Keine Auszüge auf dieser Ergebnisseite.</p> : <ul className="space-y-2">{items.map(item => <li key={item.id} className="break-words">
        <a className="underline" href={visualExtractApi.content(item.id)} target="_blank" rel="noreferrer">Seiten {item.page_start}–{item.page_end} öffnen</a>
        {' · '}{new Date(item.created_at).toLocaleString('de-DE')} · {item.reason}
        {item.partition_revision_id !== partitionId && ' · frühere Aufteilung'}
        <button className="btn ml-2" disabled={blocked || !!pending} onClick={() => setDisclosure(item)}>Freigabe dieses Auszugs prüfen</button>
      </li>)}</ul>}
      {next && <button className="btn" disabled={blocked || loading} onClick={() => setCursor(next)}>Ältere Auszüge</button>}
    </div>}
    {disclosure && <VisualDisclosurePanel key={disclosure.id} artifact={disclosure} canRead={canRead && !accessDenied}
      canPublish={canGenerate} parentGeneration={parentGeneration} onPendingChange={setDisclosurePending} />}
  </section>;
}
