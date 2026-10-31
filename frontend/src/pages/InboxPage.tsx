import { formatUploadLimit, useUploadLimit } from '../lib/uploads';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/useAuth';
import { fileDigest, intakeApi, intakeMime, type IntakeCreate, type IntakeHeader } from '../api/intake';
import { SearchChoice } from '../components/SearchChoice';

function NewIntake() {
  const maxUploadBytes = useUploadLimit();
  const navigate = useNavigate();
  const [files, setFiles] = useState<File[]>([]);
  const [scope, setScope] = useState<{ id: string; label: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState('');
  const [batch, setBatch] = useState<IntakeHeader | null>(null);
  const command = useRef<{ key: string; body: IntakeCreate } | null>(null);
  const received = useRef(new Set<string>());

  async function submit() {
    if (busy) return;
    setError(null);
    if (!files.length || files.length > 100 || files.some(file => !file.size || file.name.length > 255 || file.size > maxUploadBytes || !intakeMime(file))) {
      setError(`Bitte 1 bis 100 PDF-, XML-, PNG- oder JPEG-Dateien mit jeweils höchstens ${formatUploadLimit(maxUploadBytes)} auswählen. Leere Dateien sind nicht möglich.`); return;
    }
    setBusy(true);
    try {
      if (!command.current) {
        const items = [];
        for (const file of files) {
          setProgress(`Bereite ${file.name} vor…`);
          items.push({ client_item_key: crypto.randomUUID(), filename: file.name, mime_type: intakeMime(file),
            content_sha256: await fileDigest(file), content_byte_size: file.size });
        }
        command.current = { key: crypto.randomUUID(), body: { scope_hint_id: scope?.id ?? null, items } };
        setSubmitted(true);
      }
      const request = command.current;
      const current = batch ?? await intakeApi.create(request.body, request.key);
      setBatch(current);
      const failed: string[] = [];
      for (const [index, item] of request.body.items.entries()) {
        if (received.current.has(item.client_item_key)) continue;
        setProgress(`Übertrage Datei ${index + 1} von ${files.length}: ${item.filename}`);
        try {
          await intakeApi.upload(current.id, item.client_item_key, files[index], item.mime_type);
          received.current.add(item.client_item_key);
        } catch { failed.push(item.filename); }
      }
      if (failed.length) setError(`Für ${failed.length} Datei(en) fehlt die Empfangsbestätigung: ${failed.join(', ')}. Wiederholen Sie die Übertragung oder öffnen Sie den Stapel.`);
      else navigate(`/posteingang/${current.id}`);
    } catch {
      setError('Keine bestätigte Antwort erhalten. Wiederholen Sie denselben Auftrag; Ihre Auswahl bleibt erhalten.');
    } finally { setBusy(false); setProgress(''); }
  }

  return <section className="card min-w-0 space-y-3" aria-label="Neuer Eingang">
    <h2>Dateien einreichen</h2>
    <p>Bis zu 100 Dateien, jeweils höchstens {formatUploadLimit(maxUploadBytes)}. Jede Datei wird einzeln übertragen und vor dem Originalabruf geprüft.</p>
    <fieldset disabled={busy || submitted} className="min-w-0 space-y-3">
      <label className="block">Dateien auswählen
        <input className="block max-w-full" type="file" multiple accept=".pdf,.xml,.png,.jpg,.jpeg" onChange={event => setFiles(Array.from(event.target.files ?? []))} />
      </label>
      <SearchChoice<{ id: string; label: string }> label="Abrechnungskreis als Hinweis (optional)"
        endpoint="/api/accounting-scopes/page?capability=prepare&financial_creation=true" value={scope} onChange={setScope} display={item => item.label} />
    </fieldset>
    <p className="text-sm text-muted-foreground">Der Hinweis ordnet keine Kosten zu und erteilt keine Freigabe. Der Eingang bleibt Ihnen mit Ihren aktuellen Rechten zugeordnet.</p>
    {files.length > 0 && <p>{files.length} Datei(en) ausgewählt</p>}
    {progress && <p role="status" className="break-words">{progress}</p>}
    {error && <p role="alert" className="error-text break-words">{error}</p>}
    {batch && <p><Link to={`/posteingang/${batch.id}`}>Gespeicherten Stapel und fehlende Dateien öffnen</Link></p>}
    <button className="btn btn--primary" disabled={busy || !files.length} onClick={() => void submit()}>
      {busy ? 'Übertragung läuft…' : submitted ? 'Denselben Auftrag wiederholen' : 'Stapel einreichen'}
    </button>
  </section>;
}

export function InboxPage() {
  const { session } = useAuth();
  const canWrite = session?.role === 'OWNER_ADMIN' || session?.role === 'CLERK';
  const [rows, setRows] = useState<IntakeHeader[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(null);
    intakeApi.list(undefined, controller.signal).then(page => {
      if (!controller.signal.aborted) { setRows(page.items); setCursor(page.next_cursor); }
    }).catch(() => { if (!controller.signal.aborted) { setRows([]); setError('Die Eingänge konnten nicht geladen werden. Bitte erneut versuchen.'); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [generation]);
  async function more() {
    if (!cursor || busy) return;
    setBusy(true);
    try { const page = await intakeApi.list(cursor); setRows(value => [...value, ...page.items]); setCursor(page.next_cursor); }
    catch { setError('Weitere Eingänge konnten nicht geladen werden.'); }
    finally { setBusy(false); }
  }
  return <div className="min-w-0 space-y-5">
    <header><h1>Posteingang</h1><p>Eigene Dokumentenstapel einreichen und ihren Verarbeitungsstand wieder aufrufen.</p></header>
    <Link className="btn" to="/klaerungsfaelle">Klärungsfälle öffnen</Link>
    {canWrite && <NewIntake />}
    <section className="space-y-3" aria-label="Gespeicherte Eingänge">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2>Ihre Eingänge</h2><button className="btn" disabled={busy} onClick={() => setGeneration(value => value + 1)}>Liste aktualisieren</button></div>
      {error && <p role="alert" className="error-text">{error}</p>}
      {!busy && !error && !rows.length && <p>Noch keine zugänglichen Eingänge vorhanden.</p>}
      {rows.map(row => <article className="card flex flex-wrap items-center justify-between gap-3" key={row.id}>
        <div><h3>{row.item_count} Datei(en)</h3><p>{new Date(row.created_at).toLocaleString('de-DE')}</p></div>
        <Link className="btn" to={`/posteingang/${row.id}`}>Stapel öffnen</Link>
      </article>)}
      {busy && <p role="status">Lade Eingänge…</p>}
      {cursor && <button className="btn" disabled={busy} onClick={() => void more()}>Weitere Eingänge</button>}
    </section>
  </div>;
}
