import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { allocationAdoptionsApi, visualAllocationApi, type VisualAdoptionCommand, type VisualAdoptionPreview } from '../../api/allocationAdoptions';
import { visualExtractApi, type VisualExtract } from '../../api/visualExtracts';
import { visualDisclosureApi, type VisualDisclosure } from '../../api/visualDisclosures';

export function VisualAllocationAdoptionPanel({ logicalId, reviewId, enabled, parentBusy, generation, onPending, onConfirmed }: {
  logicalId: string; reviewId: string | null; enabled: boolean; parentBusy: boolean; generation: number;
  onPending: (value: boolean) => void; onConfirmed: () => void;
}) {
  const [artifacts, setArtifacts] = useState<VisualExtract[]>([]);
  const [artifact, setArtifact] = useState(''); const [artifactCursor, setArtifactCursor] = useState<string | null>(null);
  const [disclosures, setDisclosures] = useState<VisualDisclosure[]>([]);
  const [disclosure, setDisclosure] = useState(''); const [disclosureCursor, setDisclosureCursor] = useState<string | null>(null);
  const [preview, setPreview] = useState<VisualAdoptionPreview | null>(null);
  const [reason, setReason] = useState(''); const [costs, setCosts] = useState(false); const [retained, setRetained] = useState(false);
  const [sending, setSending] = useState(false);
  const commandEpoch = useRef(0);
  const [busy, setBusy] = useState(false); const [unknown, setUnknown] = useState(false); const [authorized, setAuthorized] = useState(false);
  const [error, setError] = useState<string | null>(null); const [refresh, setRefresh] = useState(0);
  const request = useRef<AbortController | null>(null);
  const pending = useRef<{ logical: string; body: VisualAdoptionCommand; key: string } | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort(); }; }, []);
  useEffect(() => {
    request.current?.abort(); setPreview(null); setAuthorized(false); setCosts(false); setRetained(false); setError(null);
    setArtifacts([]); setDisclosures([]); setArtifact(''); setDisclosure(''); setArtifactCursor(null); setDisclosureCursor(null);
    const targetReview = pending.current?.body.treatment_review_id ?? reviewId;
    if (!enabled || parentBusy || !targetReview) { setBusy(false); return; }
    const controller = new AbortController(); request.current = controller; setBusy(true);
    // This read checks live original authority even if a transient publication
    // vanished after an ambiguous financial command. Exact retries stay frozen.
    Promise.all([allocationAdoptionsApi.preview(targetReview, controller.signal),
      visualExtractApi.history(pending.current?.logical ?? logicalId, null, controller.signal)]).then(([, page]) => {
      if (controller.signal.aborted) return;
      setArtifacts(page.items); setArtifactCursor(page.next_cursor); setAuthorized(true);
    }).catch(failure => {
      if (controller.signal.aborted) return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
        commandEpoch.current += 1; pending.current = null; setUnknown(false); setSending(false); onPending(false); setReason('');
      }
      setError('Auszüge derzeit nicht zugänglich. Berechtigungen prüfen und erneut laden.');
    }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [logicalId, reviewId, enabled, parentBusy, generation, refresh, onPending]);
  const blocked = busy || parentBusy || unknown || sending;
  async function load(kind: 'artifacts' | 'disclosures' | 'preview', selected = artifact, cursor: string | null = null) {
    if (blocked || !authorized || !reviewId) return;
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setBusy(true); setError(null); setCosts(false); setRetained(false); setPreview(null);
    try {
      if (kind === 'artifacts') {
        const page = await visualExtractApi.history(logicalId, cursor, controller.signal);
        if (controller.signal.aborted) return;
        setArtifacts(prior => [...prior, ...page.items]); setArtifactCursor(page.next_cursor);
      } else if (kind === 'disclosures') {
        const page = await visualDisclosureApi.list(`/api/visual-extracts/${selected}/disclosures`, cursor, controller.signal);
        if (controller.signal.aborted) return;
        setDisclosures(prior => cursor ? [...prior, ...page.items] : page.items); setDisclosureCursor(page.next_cursor);
      } else {
        const result = await visualAllocationApi.preview(reviewId, disclosure, controller.signal);
        if (!controller.signal.aborted) setPreview(result);
      }
    } catch (failure) {
      if (controller.signal.aborted) return;
      if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
        setAuthorized(false); setArtifacts([]); setDisclosures([]); setReason('');
      }
      setError('Aktueller Auszug und Freigabe konnten nicht vollständig geprüft werden. Bitte erneut laden.');
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  async function save() {
    if (busy || sending || parentBusy || !authorized) return;
    if (!pending.current) {
      if (!reviewId || !preview?.can_adopt || !preview.preview_digest || !preview.visual_content_sha256 || !disclosure || !costs || !retained || !reason.trim()) return;
      pending.current = { logical: logicalId, key: crypto.randomUUID(), body: { treatment_review_id: reviewId, disclosure_id: disclosure,
        visual_content_sha256: preview.visual_content_sha256, preview_digest: preview.preview_digest, reason: reason.trim(),
        costs_confirmed: true, retained_visual_source_reviewed: true } };
    }
    const command = pending.current; const epoch = commandEpoch.current; setSending(true); onPending(true); setError(null);
    try {
      await visualAllocationApi.create(command.logical, command.body, command.key);
      if (!alive.current || commandEpoch.current !== epoch) return;
      pending.current = null; setUnknown(false); onPending(false); onConfirmed();
    } catch (failure) {
      if (!alive.current || commandEpoch.current !== epoch) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setUnknown(false); onPending(false); setPreview(null); setCosts(false); setRetained(false);
        if ([401, 403, 404].includes(failure.status)) { setAuthorized(false); setArtifacts([]); setDisclosures([]); setReason(''); }
        setError('Übernahme nicht bestätigt. Aktuelle Freigabe, Prüfung und Berechtigungen erneut laden.');
      } else { setUnknown(true); setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Kostenübernahme erneut, um ihren Ausgang festzustellen.'); }
    } finally { if (alive.current && commandEpoch.current === epoch) setSending(false); }
  }
  const available = disclosures.filter(item => item.receipt.id === item.current.id && item.receipt.kind === 'published' && item.current.available && item.current.can_download);
  return <div hidden={!enabled} className="min-w-0 space-y-3" aria-label="Auszug als Finanzbeleg">
    <p>Wählen Sie einen bereits geprüften und für den Zielbereich freigegebenen Auszug. Er wird bei der Kostenübernahme dauerhaft als Finanzbeleg aufbewahrt. Ein späterer Widerruf der vorübergehenden Freigabe entfernt diesen Finanzbeleg nicht.</p>
    <button className="btn" disabled={busy || sending || parentBusy} onClick={() => setRefresh(value => value + 1)}>Auszüge neu laden</button>
    {(busy || sending) && <p role="status">Prüfe Auszug und Kostenübernahme…</p>}{error && <p role="alert" className="error-text">{error}</p>}
    {!unknown && authorized && <>
      <label className="block">Geprüfter Auszug<select className="block w-full min-w-0" value={artifact} disabled={blocked} onChange={event => {
        const id = event.target.value; setArtifact(id); setDisclosure(''); setDisclosures([]); setDisclosureCursor(null); setPreview(null); if (id) void load('disclosures', id);
      }}><option value="">Auszug auswählen</option>{artifacts.map(item => <option key={item.id} value={item.id}>Seiten {item.page_start}–{item.page_end} · {new Date(item.created_at).toLocaleString('de-DE')} · {item.content_sha256.slice(0, 12)}</option>)}</select></label>
      {artifactCursor && <button className="btn" disabled={blocked} onClick={() => void load('artifacts', artifact, artifactCursor)}>Weitere Auszüge laden</button>}
      {artifact && <>
        <label className="block">Freigabe im Abrechnungskreis<select className="block w-full min-w-0" value={disclosure} disabled={blocked} onChange={event => { setDisclosure(event.target.value); setPreview(null); setCosts(false); setRetained(false); }}><option value="">Freigabe auswählen</option>{available.map(item => <option key={item.receipt.id} value={item.receipt.id}>{item.destination?.label ?? 'Freigegebener Abrechnungskreis'} · {new Date(item.receipt.created_at).toLocaleString('de-DE')}</option>)}</select></label>
        {disclosureCursor && <button className="btn" disabled={blocked} onClick={() => void load('disclosures', artifact, disclosureCursor)}>Weitere Freigaben laden</button>}
        {!busy && !available.length && <p>Keine nutzbare Freigabe geladen. Prüfen Sie die Freigaben dieses Auszugs.</p>}
      </>}
      {disclosure && <><a className="block underline" href={visualDisclosureApi.content(disclosure)} target="_blank" rel="noopener noreferrer">Genau diesen freigegebenen Auszug öffnen</a><button className="btn" disabled={blocked} onClick={() => void load('preview')}>Übernahme mit Auszug prüfen</button></>}
      {preview && !preview.can_adopt && <p role="status">Übernahme derzeit nicht möglich. Aktuelle Freigabe, vollständige Kostenprüfung und Belegkonflikte prüfen.</p>}
      {preview?.can_adopt && <>
        <p>Abrechnungskreis: {preview.scope_label} · {preview.cost_count} Teilkosten · {preview.gross_amount} EUR · {preview.output_page_count} Belegseiten</p>
        <fieldset className="space-y-3" disabled={blocked}>
          <label className="block">Begründung der Übernahme mit Auszug<textarea className="block w-full min-w-0" maxLength={2000} value={reason} onChange={event => { setReason(event.target.value); setCosts(false); setRetained(false); }} /></label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={costs} onChange={event => setCosts(event.target.checked)} />Alle Teilkosten, Beträge und Verteilungen geprüft; verbindlich bestätigen.</label>
          <label className="flex items-start gap-2"><input type="checkbox" checked={retained} onChange={event => setRetained(event.target.checked)} />Genau diesen Auszug geöffnet und geprüft; dauerhafte Aufbewahrung als Finanzbeleg für diesen Abrechnungskreis bestätigen.</label>
        </fieldset>
        <button className="btn" disabled={blocked || !costs || !retained || !reason.trim()} onClick={() => void save()}>Teilkosten bestätigen und Auszug aufbewahren</button>
      </>}
    </>}
    {unknown && <button className="btn" disabled={busy || sending || parentBusy || !authorized} onClick={() => void save()}>Dieselbe Übernahme mit Auszug erneut senden</button>}
  </div>;
}
