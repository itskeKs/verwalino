import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { contextMappingsApi, type ContextMapping, type MappingCommand, type MappingOption, type MappingOptions } from '../../api/contextMappings';

type Draft = { target: string; option: MappingOption; start: string; end: string; reason: string; consent: boolean };
const denied = (error: unknown) => error instanceof ApiError && [401, 403, 404].includes(error.status);
export function ContextMappingPanel({ allocationId, parentGeneration, parentBusy }: { allocationId: string | null; parentGeneration: number; parentBusy: boolean }) {
  const [view, setView] = useState<MappingOptions | null>(null); const [draft, setDraft] = useState<Draft | null>(null);
  const [receipt, setReceipt] = useState<ContextMapping | null>(null); const [busy, setBusy] = useState(false); const [sending, setSending] = useState(false);
  const [receiptTarget, setReceiptTarget] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0); const [submitted, setSubmitted] = useState(false); const [conflict, setConflict] = useState(false); const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ body: MappingCommand; expected: string | null; key: string } | null>(null);
  const alive = useRef(true); useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const target = draft?.target ?? allocationId ?? receiptTarget; const kind = draft?.option.kind ?? receipt?.identifier_kind; const value = draft?.option.value ?? receipt?.identifier_value;
  useEffect(() => {
    const controller = new AbortController(); setView(null); setBusy(!!target);
    if (target) contextMappingsApi.options(target, kind && value ? { kind, value } : null, controller.signal).then(result => {
      if (!controller.signal.aborted) setView(result);
    }).catch(failure => { if (!controller.signal.aborted) {
      if (denied(failure)) { pending.current = null; setSubmitted(false); setDraft(null); setReceipt(null); setReceiptTarget(null); }
      setError('Gespeicherte Zuordnungen derzeit nicht verfügbar. Bitte erneut laden.');
    } }).finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [target, kind, value, generation, parentGeneration]);
  const blocked = busy || sending || parentBusy;
  const current = draft && view?.options.find(option => option.kind === draft.option.kind && option.value === draft.option.value && option.page_number === draft.option.page_number);
  const stale = conflict || !!draft && (!current || current.current?.id !== draft.option.current?.id);
  const authorized = !!view && view.allocation_set_id === target;
  async function save() {
    if (blocked || !authorized || !draft || (!pending.current && (stale || !view?.can_activate || !draft.start || !draft.end || draft.start > draft.end || !draft.reason.trim() || !draft.consent))) return;
    if (!pending.current) pending.current = { key: crypto.randomUUID(), expected: draft.option.current?.id ?? null,
      body: { allocation_set_id: draft.target, identifier_kind: draft.option.kind, identifier_value: draft.option.value, page_number: draft.option.page_number,
        valid_from: draft.start, valid_to: draft.end, reason: draft.reason.trim(), reuse_consented: true } };
    const request = pending.current; setSending(true); setSubmitted(true); setError(null);
    try {
      const result = await contextMappingsApi.activate(request.body, request.expected, request.key);
      if (!alive.current) return;
      pending.current = null; setSubmitted(false); setDraft(null); setReceipt(result); setReceiptTarget(request.body.allocation_set_id); setConflict(false); setGeneration(n => n + 1);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && [401, 403, 404, 409, 422, 428].includes(failure.status)) {
        pending.current = null; setSubmitted(false);
        if (denied(failure)) { setDraft(null); setView(null); setReceipt(null); setReceiptTarget(null); }
        if (failure.status === 409) setConflict(true);
        setError('Speicherung nicht bestätigt. Aktuelle Grundlage und Berechtigung erneut prüfen.'); setGeneration(n => n + 1);
      } else setError('Keine bestätigte Antwort erhalten. Senden Sie dieselbe Speicherung erneut.');
    } finally { if (alive.current) setSending(false); }
  }
  if (!target && !receipt) return null;
  return <section className="min-w-0 space-y-3 border-t border-input pt-4" aria-label="Zuordnung für weitere Belege speichern">
    <div className="flex flex-wrap justify-between gap-3"><h3>Zuordnung für weitere Belege speichern</h3><button className="btn" disabled={blocked} onClick={() => setGeneration(n => n + 1)}>Gespeicherte Zuordnung aktualisieren</button></div>
    <p>Speichern Sie eine geprüfte Kennung ausdrücklich als Hinweis für weitere Belege. Jeder spätere Beleg und sein Abrechnungskreis müssen erneut geprüft werden.</p>
    {busy && <p role="status">Prüfe wiederverwendbare Kennungen…</p>}{error && <p role="alert">{error}</p>}
    {receipt && authorized && receiptTarget === target && <p>Speicherung bestätigt, Version {receipt.revision_number}. <a className="underline" href={`/zuordnungen/${receipt.id}`}>Aktuellen Zustand prüfen oder widerrufen</a></p>}
    {view && authorized && <>
      <p className="break-words">Ziel: {view.scope_label ?? 'übernommener Abrechnungskreis'}</p>
      {(!view.can_activate || !view.options.length) && <p>Auf dieser Grundlage kann derzeit keine unterstützte Kennung gespeichert werden.</p>}
      {view.options.map((option, index) => <div className="space-y-2" key={`${option.kind}:${option.value}:${index}`}>
        <p className="break-words">{option.kind === 'property_id' ? 'Objekt-ID' : 'Zählernummer'} · Seite {option.page_number}: „{option.quotation}“</p>
        {option.current && <a className="block underline" href={`/zuordnungen/${option.current.id}`}>Gespeicherte Version {option.current.revision_number} öffnen ({option.current.state === 'active' ? 'aktiv' : 'widerrufen'})</a>}
        <button className="btn btn--wrap" disabled={blocked || submitted || !view.can_activate} onClick={() => { setReceipt(null); setReceiptTarget(null); setDraft({ target: view.allocation_set_id, option, start: '', end: '', reason: '', consent: false }); setConflict(false); setError(null); }}>Kennung auswählen: {option.value}</button>
      </div>)}
    </>}
    {draft && authorized && <>
      {stale && !submitted && <p>Grundlage geändert. Wählen Sie eine aktuelle Kennung erneut aus.</p>}
      <p className="break-words">Ausgewählt: {draft.option.value}</p>
      {draft.option.kind === 'meter_number' && <p>Eine Zählernummer belegt keine frühere Installation oder Zuständigkeit für das gesamte Objekt.</p>}
      <fieldset className="min-w-0 space-y-3" disabled={blocked || submitted || stale || !view?.can_activate}>
        <label className="block">Leistungszeitraum gültig ab<input type="date" className="block max-w-full rounded-md border border-input bg-background px-3 py-2" value={draft.start} onChange={e => setDraft({ ...draft, start: e.target.value, consent: false })} /></label>
        <label className="block">Leistungszeitraum gültig bis<input type="date" className="block max-w-full rounded-md border border-input bg-background px-3 py-2" value={draft.end} onChange={e => setDraft({ ...draft, end: e.target.value, consent: false })} /></label>
        <label className="block">Begründung der wiederverwendbaren Zuordnung<textarea className="w-full rounded-md border border-input bg-background p-2" maxLength={2000} value={draft.reason} onChange={e => setDraft({ ...draft, reason: e.target.value, consent: false })} /></label>
        <label className="flex items-start gap-2"><input type="checkbox" checked={draft.consent} onChange={e => setDraft({ ...draft, consent: e.target.checked })} />Kennung, Ziel und Zeitraum geprüft. Berechtigte Personen dieses Objekts dürfen diese Zuordnung als Hinweis wiederverwenden.</label>
      </fieldset>
      <button className="btn btn--wrap" disabled={blocked || (!submitted && (stale || !view?.can_activate || !draft.start || !draft.end || draft.start > draft.end || !draft.reason.trim() || !draft.consent))} onClick={() => void save()}>{submitted ? 'Dieselbe Zuordnung erneut speichern' : 'Zuordnung zur Wiederverwendung speichern'}</button>
    </>}
  </section>;
}
