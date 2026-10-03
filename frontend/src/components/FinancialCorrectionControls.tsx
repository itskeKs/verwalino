import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { createSettlementCorrection, getWegRun } from '../api/endpoints';
import { financialCorrectionsApi, type PlanSuccessorPreview, type PlanSuccessorSelection } from '../api/financialCorrections';
import type { WegRun } from '../api/types';

export function FinancialCorrectionControls({ run }: { run: WegRun }) {
  const [busy, setBusy] = useState(false); const [unknown, setUnknown] = useState(false);
  const [successor, setSuccessor] = useState(run.superseded_by_id);
  const [confirmed, setConfirmed] = useState(false); const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  if (run.status !== 'finalized') return null;
  const base = run.run_type === 'VERMOEGENSBERICHT' ? '/vermoegensberichte/' : '/weg-abrechnungen/';
  async function create() {
    if (busy || !confirmed || successor || unknown) return;
    setBusy(true); setError(null);
    try { const result = await createSettlementCorrection(run.id); if (alive.current) setSuccessor(result.id); }
    catch (failure) {
      if (!alive.current) return;
      setConfirmed(false);
      if (failure instanceof ApiError && [401, 403, 404, 422].includes(failure.status)) setError('Korrektur derzeit nicht zulässig oder nicht zugänglich. Vorgang erneut öffnen.');
      else { setUnknown(true); setError('Ausgang noch nicht bestätigt. Prüfen Sie zuerst, ob ein Korrekturentwurf angelegt wurde.'); }
    } finally { if (alive.current) setBusy(false); }
  }
  async function recover() {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const current = await getWegRun(run.id);
      if (alive.current) { setSuccessor(current.superseded_by_id); setUnknown(false); setConfirmed(false); }
    } catch { if (alive.current) setError('Der Ausgang ist derzeit nicht prüfbar. Bitte erneut versuchen.'); }
    finally { if (alive.current) setBusy(false); }
  }
  return <section className="card space-y-3" aria-label="Finanzvorgang korrigieren">
    <h2>Korrektur</h2>
    {successor ? <Link className="link" to={base + successor}>Korrekturvorgang öffnen</Link> : <>
      <p>Ein neuer Entwurf übernimmt Objekt und Zeitraum. Eingaben, Datengrundlage und Berechnung müssen erneut geprüft werden. Das festgeschriebene Original bleibt erhalten.</p>
      {!unknown && <><label className="flex items-start gap-2"><input type="checkbox" disabled={busy} checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />Neuen Korrekturentwurf für diesen Vorgang anlegen.</label>
        <button className="btn" disabled={busy || !confirmed} onClick={() => void create()}>Korrekturentwurf anlegen</button></>}
    </>}
    {unknown && <button className="btn" disabled={busy} onClick={() => void recover()}>Ausgang der Korrektur prüfen</button>}
    {busy && <p role="status">Korrektur wird geprüft…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}

export function PlanSuccessorSelector({ runId, onApplied, disabled = false, onBlockedChange }: {
  runId: string; onApplied: () => void; disabled?: boolean; onBlockedChange?: (blocked: boolean) => void;
}) {
  const [preview, setPreview] = useState<PlanSuccessorPreview | null>(null);
  const [pending, setPending] = useState<PlanSuccessorSelection | null>(null);
  const [reason, setReason] = useState(''); const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null); const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort(); }; }, []);
  useEffect(() => { onBlockedChange?.(busy || !!pending); }, [busy, pending, onBlockedChange]);
  useEffect(() => () => onBlockedChange?.(false), [onBlockedChange]);
  async function load() {
    if (disabled || busy || pending) return;
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setPreview(null); setConfirmed(false); setError(null);
    try { const result = await financialCorrectionsApi.previewPlan(runId, controller.signal); if (!controller.signal.aborted) setPreview(result); }
    catch { if (!controller.signal.aborted) setError('Kein vollständig zugänglicher, festgeschriebener Nachfolger mit passendem Abrechnungskontext verfügbar. Wirtschaftsplan prüfen.'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  async function apply() {
    if (disabled || busy) return;
    const command = pending ?? (preview?.successor_id && confirmed && reason.trim() ? {
      expected_plan_id: preview.current_plan_id, successor_id: preview.successor_id, reason: reason.trim(), confirmed: true as const,
    } : null);
    if (!command) return;
    setPending(command); setBusy(true); setError(null);
    try { await financialCorrectionsApi.selectPlan(runId, command); if (alive.current) { setPending(null); onApplied(); } }
    catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422].includes(failure.status)) {
        setPending(null); setPreview(null); setConfirmed(false); setError('Übernahme nicht bestätigt. Aktuellen Nachfolger erneut prüfen.');
      } else setError('Keine bestätigte Antwort erhalten. Dieselbe Auswahl erneut senden, um ihren Ausgang festzustellen.');
    } finally { if (alive.current) setBusy(false); }
  }
  return <section className="card min-w-0 space-y-3" aria-label="Korrigierten Wirtschaftsplan auswählen">
    <h2>Wirtschaftsplan prüfen</h2>
    <button className="btn" disabled={disabled || busy || !!pending} onClick={() => void load()}>Nachfolger des Wirtschaftsplans prüfen</button>
    {preview && !preview.successor_id && <p role="status">Der verknüpfte Wirtschaftsplan ist aktuell.</p>}
    {preview?.successor_id && <>
      <p>Festgeschriebener Nachfolger für {preview.plan_year} · {preview.period_start} bis {preview.period_end}</p>
      <a className="link" href={`/wirtschaftsplaene/${preview.successor_id}`} target="_blank" rel="noreferrer">Nachfolger prüfen</a>
      <fieldset disabled={disabled || busy || !!pending} className="min-w-0 space-y-3">
        <legend>Nachfolger übernehmen</legend>
        <p>Die bisherige Entwurfsberechnung wird zurückgesetzt. Prüfen Sie anschließend Zahlungen und Datengrundlage und berechnen Sie erneut.</p>
        <label className="block">Grundlage der Auswahl<textarea className="block w-full" maxLength={2000} value={reason} onChange={event => { setReason(event.target.value); setConfirmed(false); }} /></label>
        <label className="flex items-start gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />Nachfolger und seine Soll-Vorschüsse für diesen Abrechnungskontext geprüft.</label>
        <button className="btn" disabled={disabled || busy || !!pending || !confirmed || !reason.trim()} onClick={() => void apply()}>Geprüften Wirtschaftsplan übernehmen</button>
      </fieldset>
    </>}
    {pending && <button className="btn" disabled={disabled || busy} onClick={() => void apply()}>Dieselbe Planauswahl erneut senden</button>}
    {busy && <p role="status">Wirtschaftsplan wird geprüft…</p>}
    {error && <p role="alert" className="error-text">{error}</p>}
  </section>;
}
