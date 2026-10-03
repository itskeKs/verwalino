import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ApiError } from '../api/client';
import { fileDigest, intakeApi, type IntakeBatch, type IntakeItem, type IntakeStatus } from '../api/intake';

const labels: Record<IntakeStatus, string> = { waiting: 'Datei fehlt', received: 'Empfangen', processing: 'In Verarbeitung', review: 'Zur Sichtung', confirmed: 'Bestätigt', failed: 'Gesperrt / fehlgeschlagen' };
const stageLabels = { inspection: 'Dateiprüfung', preparation: 'Dokumentvorbereitung', extraction: 'Texterkennung', review: 'Vorschläge verfügbar' };
const explanations: Record<string, string> = {
  scanner_unavailable: 'Die Dateiprüfung ist derzeit nicht verfügbar. Die Datei bleibt gesperrt. Nach Behebung kann die Prüfung wiederholt werden.',
  scanner_timeout: 'Die Dateiprüfung hat zu lange gedauert. Ein erneuter Versuch ist möglich.',
  inspection_timeout: 'Die Prüfung wurde wegen Zeitüberschreitung beendet.',
  scanner_detected: 'Die Sicherheitsprüfung hat die Datei zurückgewiesen. Das Original bleibt gesperrt.',
  encrypted_pdf: 'Verschlüsselte PDFs können nicht verarbeitet werden. Bitte eine unverschlüsselte Fassung neu einreichen.',
  invalid_document: 'Die Datei ist beschädigt oder nicht lesbar. Bitte eine korrigierte Fassung neu einreichen.',
  mime_mismatch: 'Dateiinhalt und Dateityp stimmen nicht überein. Bitte die Datei prüfen und neu einreichen.',
  initiator_permission_changed: 'Die erforderlichen Rechte sind nicht mehr verfügbar oder die Verarbeitung wurde pausiert.',
  attempt_limit: 'Die Höchstzahl der Prüfversuche ist erreicht. Bitte die Ursache klären, bevor Sie einen neuen Eingang anlegen.',
  storage_integrity_mismatch: 'Das gespeicherte Original konnte nicht unverändert bestätigt werden. Bitte die Administration informieren.',
  stored_original_unavailable: 'Das gespeicherte Original ist derzeit nicht verfügbar.',
  ocr_unavailable: 'Die lokale Texterkennung ist derzeit nicht verfügbar. Ein erneuter Auftrag ist in der Belegansicht möglich.',
  ocr_timeout: 'Die Texterkennung hat zu lange gedauert. Bitte den Verarbeitungsstand in der Belegansicht prüfen.',
  extraction_timeout: 'Die Texterkennung wurde wegen Zeitüberschreitung beendet.',
  processor_failed: 'Die Texterkennung konnte kein gültiges Ergebnis liefern. Bitte die Belegansicht öffnen.',
};

function ItemActions({ item, batchId, refresh, disabled }: { item: IntakeItem; batchId: string; refresh: () => void; disabled: boolean }) {
  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const retry = useRef<{ key: string; body: { job_id: string; expected_fence: string; reason: string } } | null>(null);
  async function upload() {
    if (!file || busy) return;
    setBusy(true); setError(null);
    try {
      if (file.size !== item.content_byte_size || await fileDigest(file) !== item.content_sha256) {
        setError('Bitte genau die ursprünglich ausgewählte Datei verwenden. Für eine geänderte Fassung einen neuen Stapel anlegen.'); return;
      }
      await intakeApi.upload(batchId, item.client_item_key, file, item.mime_type); refresh();
    } catch { setError('Keine Empfangsbestätigung erhalten. Wiederholen Sie die Übertragung derselben Datei.'); }
    finally { setBusy(false); }
  }
  async function retryInspection() {
    if (busy || !reason.trim() || !item.job_id || !item.fence) return;
    retry.current ??= { key: crypto.randomUUID(), body: { job_id: item.job_id, expected_fence: item.fence, reason: reason.trim() } };
    setBusy(true); setSubmitted(true); setError(null);
    try { await intakeApi.retry(retry.current.body, retry.current.key); refresh(); }
    catch (failure) { setError(failure instanceof ApiError && [403, 404, 409].includes(failure.status)
      ? 'Der Prüfstand oder Ihre Berechtigung hat sich geändert. Bitte den Stapel aktualisieren.'
      : 'Keine bestätigte Antwort erhalten. Wiederholen Sie denselben Prüfauftrag.'); }
    finally { setBusy(false); }
  }
  return <fieldset disabled={disabled} className="min-w-0 space-y-2">
    {!disabled && item.can_export && item.original_id && <a className="btn" href={intakeApi.content(item.original_id)}>Original herunterladen</a>}
    {item.can_upload && <div className="space-y-2">
      <label className="block">Fehlende Datei erneut auswählen: {item.filename}<input className="block max-w-full" type="file" disabled={busy} onChange={event => setFile(event.target.files?.[0] ?? null)} /></label>
      <button className="btn" disabled={busy || !file} onClick={() => void upload()}>Datei übertragen</button>
    </div>}
    {item.can_retry && <div className="space-y-2">
      <label className="block" htmlFor={`retry-${item.id}`}>Grund für die Wiederholung</label>
      <input id={`retry-${item.id}`} className="w-full rounded-md border border-input bg-background px-3 py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:opacity-60" maxLength={1000} value={reason} disabled={busy || submitted} onChange={event => setReason(event.target.value)} />
      <button className="btn" disabled={busy || !reason.trim()} onClick={() => void retryInspection()}>{submitted ? 'Denselben Prüfauftrag wiederholen' : 'Prüfung erneut anfordern'}</button>
    </div>}
    {busy && <p role="status">Auftrag läuft…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </fieldset>;
}

export function IntakeBatchPage() {
  const { batchId = '' } = useParams();
  return <BatchDetail key={batchId} batchId={batchId} />;
}

function BatchDetail({ batchId }: { batchId: string }) {
  const [batch, setBatch] = useState<IntakeBatch | null>(null);
  const [limit, setLimit] = useState(50);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const refresh = useCallback(() => setGeneration(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(null);
    intakeApi.get(batchId, limit, controller.signal).then(value => { if (!controller.signal.aborted) setBatch(value); })
      .catch(failure => { if (!controller.signal.aborted) {
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) setBatch(null);
        setError('Der aktuelle Stand konnte nicht geladen werden. Bitte Verbindung und Berechtigung prüfen und erneut laden.');
      } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [batchId, limit, generation]);
  const active = !!batch && batch.counts.received + batch.counts.processing > 0;
  useEffect(() => {
    if (!active || error || busy) return;
    const timer = window.setTimeout(refresh, 3000);
    return () => window.clearTimeout(timer);
  }, [active, batch?.revision, generation, refresh, error, busy]);
  return <div className="min-w-0 space-y-5">
    <Link to="/posteingang">Zum Posteingang</Link>
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h1>Dokumentenstapel</h1>
      <p>Der gespeicherte Stand bleibt auch nach dem Schließen dieses Tabs erhalten.</p></div>
      <button className="btn" disabled={busy} onClick={refresh}>Stand aktualisieren</button></header>
    {error && <p role="alert" className="error-text">{error}{batch && ' Die angezeigten Daten stammen vom letzten erfolgreichen Abruf.'}</p>}
    {busy && <p role="status">Lade aktuellen Stand…</p>}
    {batch && <>
      <section className="grid grid-cols-2 gap-3 md:grid-cols-3" aria-label="Verarbeitungsstand">
        {(Object.keys(labels) as IntakeStatus[]).map(status => <div className="card min-w-0" key={status}><p className="text-2xl font-semibold">{batch.counts[status]}</p><p>{labels[status]}</p></div>)}
      </section>
      <p>Nach der Dateiprüfung werden die Belege vorbereitet und ausgelesen. „Zur Sichtung“ zeigt ungeprüfte Vorschläge und bestätigt keine Kosten, Zuordnung oder Abrechnung.</p>
      <p>{batch.item_count} Datei(en) · Eingang vom {new Date(batch.created_at).toLocaleString('de-DE')}</p>
      <div className="space-y-3">{batch.items.map(item => <article className="card min-w-0 space-y-3" key={item.id}>
        <div className="flex flex-wrap items-start justify-between gap-2"><h2 className="min-w-0 break-all">{item.filename}</h2><span className="badge">{labels[item.status]}</span></div>
        <p className="text-sm">{(item.content_byte_size / 1024).toLocaleString('de-DE', { maximumFractionDigits: 1 })} KiB · {item.mime_type}</p>
        {item.stage && <p>{stageLabels[item.stage]}{item.logical_counts?.total > 0 && ` · ${item.logical_counts.total} Beleg(e), ${item.logical_counts.review} zur Sichtung`}</p>}
        {item.error_code && <p>{explanations[item.error_code] ?? 'Die Verarbeitung konnte nicht abgeschlossen werden. Bitte den Verarbeitungsstand prüfen.'}</p>}
        {!busy && !error && item.document_ref_id && <Link className="btn" to={`/posteingang/belege/${item.document_ref_id}`}>Belege und Vorschläge ansehen</Link>}
        <ItemActions key={`${item.id}:${item.fence ?? 'waiting'}`} item={item} batchId={batchId} refresh={refresh} disabled={busy || !!error} />
      </article>)}</div>
      {batch.next_cursor && <button className="btn" disabled={busy} onClick={() => setLimit(value => Math.min(200, value + 50))}>Weitere Dateien</button>}
    </>}
  </div>;
}
