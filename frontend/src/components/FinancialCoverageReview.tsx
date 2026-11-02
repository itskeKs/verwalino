import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

export function FinancialCoverageReview({ kind, subjectId, values, onSaved }: {
  kind: "settlement" | "economic_plan"; subjectId: string; values: object;
  onSaved?: () => void;
}) {
  const [reason, setReason] = useState("");
  const [confirmedValues, setConfirmedValues] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savedValues, setSavedValues] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sources, setSources] = useState<{ id: string; original_filename: string | null; document_number: string | null; version: number }[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [cursor, setCursor] = useState<string | null>(null); const [sourcesLoaded, setSourcesLoaded] = useState(false);
  const [loadingSources, setLoadingSources] = useState(false); const sourceRequest = useRef<AbortController | null>(null);
  useEffect(() => {
    sourceRequest.current?.abort(); setSources([]); setSelectedSources([]); setSourcesLoaded(false); setCursor(null); setLoadingSources(false);
    return () => sourceRequest.current?.abort();
  }, [kind, subjectId]);
  const fingerprint = JSON.stringify({ kind, subjectId, values, reason, selectedSources });
  const complete = confirmedValues === fingerprint;
  async function loadSources(after: string | null = null) {
    sourceRequest.current?.abort(); const controller = new AbortController(); sourceRequest.current = controller;
    setLoadingSources(true); setError(null);
    if (!after) { setSources([]); setSelectedSources([]); setConfirmedValues(null); setSavedValues(null); setCursor(null); setSourcesLoaded(false); }
    try {
      const result = await api.get<{ items: typeof sources; next_cursor: string | null }>(`/api/financial-input-coverage/source-options?subject_kind=${kind}&subject_id=${encodeURIComponent(subjectId)}${after ? `&after=${encodeURIComponent(after)}` : ''}`, { signal: controller.signal });
      if (!controller.signal.aborted) { setSources(previous => after ? [...previous, ...result.items] : result.items); setCursor(result.next_cursor); setSourcesLoaded(true); }
    } catch (failure) {
      if (!controller.signal.aborted) {
        if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
          setSources([]); setSelectedSources([]); setConfirmedValues(null); setSavedValues(null); setCursor(null); setSourcesLoaded(false);
        }
        setError('Freigegebene Belege derzeit nicht verfügbar. Bitte erneut laden.');
      }
    }
    finally { if (!controller.signal.aborted) setLoadingSources(false); }
  }
  async function save() {
    if (busy || loadingSources || !complete || !reason.trim()) return;
    setBusy(true); setError(null);
    try {
      await api.post("/api/financial-input-coverage", { subject_kind: kind, subject_id: subjectId,
        values, reason, confirmed_complete: true, source_document_ids: selectedSources });
      setSavedValues(fingerprint);
      onSaved?.();
    } catch (err) { setError(err instanceof ApiError ? err.message : String(err)); }
    finally { setBusy(false); }
  }
  return <section className="card" aria-label="Datengrundlage bestätigen">
    <h2>Datengrundlage bestätigen</h2>
    <p>Fehlende Werte sind unbekannt. Auch 0,00 und leere Listen benötigen eine vollständige Prüfung für den Abrechnungszeitraum.</p>
    <label>Grundlage der Prüfung<textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Zum Beispiel: Kontoauszüge Januar bis Dezember vollständig geprüft" /></label>
    <div className="min-w-0 space-y-2">
      <button className="btn" disabled={busy || loadingSources} onClick={() => void loadSources()}>Freigegebene Belege auswählen</button>
      {loadingSources && <p role="status">Lade Belege dieses Abrechnungskreises…</p>}
      {sourcesLoaded && <fieldset disabled={busy || loadingSources} className="min-w-0 space-y-2"><legend>Geprüfte Belege im Nachweis</legend>
        {sources.length === 0 && <p>Keine freigegebenen Belege für diesen Vorgang verfügbar.</p>}
        {sources.map(source => <div key={source.id} className="flex min-w-0 items-start gap-2">
          <label className="min-w-0 break-words"><input type="checkbox" checked={selectedSources.includes(source.id)} onChange={event => setSelectedSources(previous => event.target.checked ? [...previous, source.id] : previous.filter(id => id !== source.id))} />{source.document_number || source.original_filename || 'Freigegebener Beleg'} · Version {source.version}</label>
          <a className="shrink-0 underline" href={`/belege/${source.id}`} target="_blank" rel="noreferrer">Beleg öffnen</a>
        </div>)}
      </fieldset>}
      {cursor && <button className="btn" disabled={busy || loadingSources} onClick={() => void loadSources(cursor)}>Weitere Belege laden</button>}
      {selectedSources.length > 0 && <p>{selectedSources.length} Belegnachweise ausgewählt.</p>}
    </div>
    <label><input type="checkbox" checked={complete} onChange={(e) => setConfirmedValues(e.target.checked ? fingerprint : null)} />Alle angezeigten Beträge und Listen sind vollständig geprüft, einschließlich bestätigter Nullbeträge.</label>
    <button className="btn" disabled={busy || loadingSources || !complete || !reason.trim()} onClick={save}>Datengrundlage speichern</button>
    {savedValues === fingerprint && <p role="status">Prüfung gespeichert. Geänderte Zahlungen oder Stammdaten erfordern eine neue Prüfung.</p>}
    {error && <p className="error-text" role="alert">{error}</p>}
  </section>;
}
