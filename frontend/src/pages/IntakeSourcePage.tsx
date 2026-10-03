import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { SourcePartitionPanel } from '../components/intake/SourcePartitionPanel';
import { ApiError } from '../api/client';
import { extractionApi, type ExtractionRevision, type ExtractionStatus, type LogicalDocument } from '../api/intake';

const fieldLabels: Record<string, string> = { vendor_name: 'Aussteller', document_number: 'Belegnummer', invoice_date: 'Rechnungsdatum',
  gross_amount: 'Bruttobetrag', vat_amount: 'Umsatzsteuerbetrag', vat_rate: 'Umsatzsteuersatz', service_period_from: 'Leistungszeitraum von',
  service_period_to: 'Leistungszeitraum bis', suggested_category_code: 'Vorgeschlagene Kostenart' };
const statuses: Record<string, string> = { queued: 'Wartet auf Texterkennung', running: 'Texterkennung läuft', succeeded: 'Vorschläge verfügbar',
  needs_review: 'Unvollständige Vorschläge', unavailable: 'Texterkennung nicht verfügbar', timeout: 'Zeitüberschreitung', failed: 'Verarbeitung fehlgeschlagen',
  canceled: 'Verarbeitung abgebrochen', blocked: 'Verarbeitung gesperrt', unsupported: 'Dokument nicht unterstützt' };
const inputClass = 'w-full rounded-md border border-input bg-background px-3 py-2 disabled:opacity-60';
const denied = (failure: unknown) => failure instanceof ApiError && [401, 403, 404].includes(failure.status);
const range = (document: LogicalDocument) => document.page_start === null ? 'Gesamtes XML-Dokument'
  : document.page_start === document.page_end ? `Seite ${document.page_start}` : `Seiten ${document.page_start}–${document.page_end}`;

function Proposals({ document, onDenied, parentBusy, parentGeneration }: { document: LogicalDocument; onDenied: () => void; parentBusy: boolean; parentGeneration: number }) {
  const [view, setView] = useState<{ status: ExtractionStatus; revisions: ExtractionRevision[] } | null>(null);
  const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const pending = useRef<{ key: string; body: { expected_fence: string; reason: string } } | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const refresh = useCallback(() => setGeneration(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError(null);
    Promise.all([extractionApi.status(document.id, controller.signal), extractionApi.history(document.document_ref_id, document.id, controller.signal)])
      .then(async ([status, history]) => {
        const revisions = await Promise.all(history.items.map(row => extractionApi.revision(row.id, controller.signal)));
        if (!controller.signal.aborted) {
          setView({ status, revisions });
          if (pending.current && status.fence !== pending.current.body.expected_fence) {
            pending.current = null; setSubmitted(false); setReason('');
          }
        }
      }).catch(failure => { if (!controller.signal.aborted) {
        setView(null); setError('Der aktuelle Stand ist nicht verfügbar. Bitte erneut laden.');
        if (denied(failure)) onDenied();
      } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [document.id, document.document_ref_id, generation, parentGeneration, onDenied]);
  const active = view?.status.status === 'queued' || view?.status.status === 'running';
  useEffect(() => {
    if (!active || busy || parentBusy || sending || error) return;
    const timer = window.setTimeout(refresh, 3000); return () => window.clearTimeout(timer);
  }, [active, busy, parentBusy, sending, error, refresh, generation]);
  async function retry() {
    if (busy || parentBusy || sending || !view?.status.can_retry || !view.status.fence || !reason.trim()) return;
    pending.current ??= { key: crypto.randomUUID(), body: { expected_fence: view.status.fence, reason: reason.trim() } };
    setSending(true); setSubmitted(true); setError(null);
    try { await extractionApi.retry(document.id, pending.current.body, pending.current.key); if (alive.current) refresh(); }
    catch (failure) {
      if (!alive.current) return;
      if (denied(failure)) { setView(null); onDenied(); }
      else setError(failure instanceof ApiError && failure.status === 409 ? 'Der Verarbeitungsstand hat sich geändert. Bitte erneut laden.'
        : 'Keine bestätigte Antwort erhalten. Wiederholen Sie denselben Auftrag.');
    } finally { if (alive.current) setSending(false); }
  }
  return <section className="min-w-0 space-y-4" aria-label="Extraktionsvorschläge">
    <div className="flex flex-wrap items-center justify-between gap-3"><h2>{range(document)}</h2>
      <button className="btn" disabled={busy || parentBusy || sending} onClick={refresh}>Vorschläge aktualisieren</button></div>
    {busy && <p role="status">Lade Verarbeitungsstand und Fundstellen…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
    {view && <>
      <p>{statuses[view.status.status] ?? 'Verarbeitungsstand unbekannt'} · Versuch {view.status.attempt_number} von höchstens 3</p>
      {!view.revisions.length && <p>Es liegt noch kein Extraktionsergebnis vor.</p>}
      {view.status.can_retry && <fieldset disabled={busy || parentBusy || sending} className="card min-w-0 space-y-2">
        <label htmlFor="extraction-retry-reason">Grund für die erneute Texterkennung</label>
        <input id="extraction-retry-reason" className={inputClass} maxLength={1000} value={reason} disabled={submitted} onChange={event => setReason(event.target.value)} />
        <button className="btn" disabled={!reason.trim()} onClick={() => void retry()}>{submitted ? 'Denselben Auftrag wiederholen' : 'Texterkennung erneut anfordern'}</button>
      </fieldset>}
      {view.revisions.map((revision, index) => <details key={revision.id} className="card min-w-0 space-y-3" open={index === 0}>
        <summary className="cursor-pointer">{index === 0 ? 'Neuester Versuch' : 'Früherer Versuch'} · {statuses[revision.status] ?? revision.status} · {new Date(revision.created_at).toLocaleString('de-DE')}</summary>
        {!revision.fields.length && <p>Dieser Versuch enthält keine Feldvorschläge. Frühere Ergebnisse bleiben in der Historie erhalten.</p>}
        <div className="grid min-w-0 gap-4 lg:grid-cols-2">{revision.fields.map(field => <article key={field.field_name} className="min-w-0 rounded-md border border-input p-3 space-y-2">
          <h3>{fieldLabels[field.field_name] ?? field.field_name}</h3><p className="break-words font-semibold">{field.value}</p>
          {field.confidence !== null && <p className="text-sm">Extraktionssicherheit: {Math.round(Number(field.confidence) * 100)} % · ungeprüft</p>}
          {field.locator_status === 'unlocated' ? <p>Keine eindeutige Fundstelle ermittelt.</p> : <>
            <p>{field.page_number !== null ? `Fundstelle auf Seite ${field.page_number}` : 'Fundstelle im XML'} · {field.method === 'ocr' ? 'Texterkennung' : field.method === 'xml' ? 'Strukturiertes Feld' : 'PDF-Text'}</p>
            <blockquote className="border-l-2 border-input pl-3 whitespace-pre-wrap break-words">{field.quotation}</blockquote>
            {field.xml_path && <details><summary className="cursor-pointer">XML-Fundstelle anzeigen</summary><code className="block break-all text-sm">{field.xml_path}</code></details>}
          </>}
        </article>)}</div>
      </details>)}
    </>}
  </section>;
}

export function IntakeSourcePage() {
  const { documentRefId = '' } = useParams();
  return <Source key={documentRefId} refId={documentRefId} />;
}

function Source({ refId }: { refId: string }) {
  const [documents, setDocuments] = useState<LogicalDocument[] | null>(null);
  const [selected, setSelected] = useState('');
  const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const onDenied = useCallback(() => { setDocuments(null); setError('Das Original ist nicht mehr zugänglich. Bitte Berechtigung und Verbindung prüfen.'); }, []);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError(null);
    extractionApi.children(refId, controller.signal).then(result => { if (!controller.signal.aborted) {
      setDocuments(result.items); setSelected(previous => result.items.some(item => item.id === previous) ? previous : result.items[0]?.id ?? '');
    } }).catch(failure => { if (!controller.signal.aborted) { if (denied(failure)) setDocuments(null); setError('Belege nicht verfügbar. Bitte Berechtigung und Verbindung prüfen.'); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [refId, generation]);
  const refresh = useCallback(() => setGeneration(value => value + 1), []);
  const document = documents?.find(item => item.id === selected);
  return <div className="min-w-0 space-y-5">
    <Link to="/posteingang">Zum Posteingang</Link>
    <header className="flex flex-wrap justify-between gap-3"><div><h1>Belege und Vorschläge</h1>
      <p>Aus dem Original erkannte Werte und ihre Fundstellen.</p></div><button className="btn" disabled={busy} onClick={() => setGeneration(value => value + 1)}>Belege aktualisieren</button></header>
    <p>Alle Vorschläge sind ungeprüft. Fundstellen helfen bei der Sichtung; sie bestätigen keine Kosten, Zuordnung oder Vollständigkeit.</p>
    {error && <p role="alert" className="error-text">{error}</p>}
    {busy && <p role="status">Lade Belege…</p>}
    {documents?.length === 0 && <p>Die Dokumentvorbereitung ist noch nicht abgeschlossen. Bitte den Stand erneut laden.</p>}
    <div hidden={busy || !!error} className="space-y-5">
    {!!documents?.length && <div className="max-w-xl space-y-2"><label htmlFor="logical-source">Beleg auswählen</label>
      <select id="logical-source" className={inputClass} value={selected} disabled={busy} onChange={event => setSelected(event.target.value)}>
        {documents.map(item => <option key={item.id} value={item.id}>{range(item)} · {item.origin === 'inspection' ? 'Gesamtbeleg vorgeschlagen' : 'Manuell abgegrenzt'}</option>)}
      </select></div>}
    {!!documents?.length && <SourcePartitionPanel refId={refId} documents={documents} parentBusy={busy || !!error} parentGeneration={generation} onDenied={onDenied} onCreated={refresh} />}
    {document && <Proposals key={document.id} document={document} onDenied={onDenied} parentBusy={busy || !!error} parentGeneration={generation} />}
    </div>
  </div>;
}
