import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { exceptionApi, exceptionCauses, type ExceptionCase, type ExceptionWorkflow } from '../api/exceptions';

export function ExceptionsPage() {
  const [workflow, setWorkflow] = useState<ExceptionWorkflow | ''>('open');
  const [cursor, setCursor] = useState<string>(); const [generation, setGeneration] = useState(0);
  const [page, setPage] = useState<{ items: ExceptionCase[]; next_cursor: string | null } | null>(null);
  const [busy, setBusy] = useState(true); const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setPage(null); setError(false);
    exceptionApi.list(workflow, cursor, controller.signal).then(value => { if (!controller.signal.aborted) setPage(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [workflow, cursor, generation]);
  return <div className="min-w-0 space-y-5">
    <Link to="/posteingang">Zum Posteingang</Link>
    <header className="flex flex-wrap justify-between gap-3"><div><h1>Klärungsfälle</h1><p>Zugängliche Konflikte aus Zuordnungsvorschlägen.</p></div>
      <button className="btn" disabled={busy} onClick={() => setGeneration(value => value + 1)}>Fälle aktualisieren</button></header>
    <p>Beanspruchen und Zurückstellen dokumentieren die Bearbeitung. Die fachliche Sperre bleibt bestehen.</p>
    <div className="max-w-sm space-y-2"><label htmlFor="exception-filter">Bearbeitungsstand</label>
      <select id="exception-filter" className="w-full rounded-md border border-input bg-background px-3 py-2" value={workflow}
        onChange={event => { setWorkflow(event.target.value as ExceptionWorkflow | ''); setCursor(undefined); }}>
        <option value="open">Offen</option><option value="snoozed">Zurückgestellt</option><option value="completed">Abgeschlossen</option><option value="">Alle</option>
      </select></div>
    {busy && <p role="status">Lade Klärungsfälle…</p>}
    {error && <p role="alert" className="error-text">Klärungsfälle derzeit nicht verfügbar. Bitte erneut laden.</p>}
    {page && !busy && <>
      {!page.items.length && <p>Keine zugänglichen Fälle für diesen Bearbeitungsstand.</p>}
      {page.items.map(item => <article key={item.id} className="card min-w-0 space-y-2">
        <h2><Link to={`/klaerungsfaelle/${item.id}`}>{exceptionCauses[item.cause] ?? 'Klärung erforderlich'}</Link></h2>
        <p className="break-words">{item.scope_label} · {item.scope_type}</p>
        <p>{item.workflow_state === 'snoozed' ? `Zurückgestellt bis ${new Date(item.snoozed_until!).toLocaleString('de-DE')}` : item.workflow_state === 'completed' ? 'Abgeschlossen · Fachlich geklärt' : 'Offen · Fachlich ungeklärt'}</p>
        <p>{item.claimant_id ? 'Bearbeitung beansprucht' : 'Noch nicht beansprucht'} · {item.dependency_count} verknüpfte Vorschläge</p>
      </article>)}
      <div className="flex flex-wrap gap-3">{cursor && <button className="btn" onClick={() => setCursor(undefined)}>Zur ersten Seite</button>}
        {page.next_cursor && <button className="btn" onClick={() => setCursor(page.next_cursor!)}>Nächste Fälle</button>}</div>
    </>}
  </div>;
}
