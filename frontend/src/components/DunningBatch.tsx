import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { createDunningBatch, previewDunningBatch, sumBatchAmounts } from "../api/dunningBatch";
import type { DunningBatchCreate, DunningBatchInput, DunningBatchPreview } from "../api/dunningBatch";
import type { DunningNotice } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { AccountingScopeSelector } from "./AccountingScopeSelector";

interface Props {
  periodStart: string;
  periodEnd: string;
  properties: { id: string; name: string }[];
}

function validDate(value: string): boolean {
  const date = new Date(`${value}T00:00:00Z`);
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

function amount(value: string): string | null {
  const normalized = value.trim().replace(",", ".");
  return /^\d+(?:\.\d{1,2})?$/.test(normalized) ? sumBatchAmounts([normalized]) : null;
}

export function DunningBatch({ periodStart, periodEnd, properties }: Props) {
  const { session, loading } = useAuth();
  const canWrite = !loading && (session?.role === "OWNER_ADMIN" || session?.role === "CLERK");
  const sessionKey = `${session?.organization_id}:${session?.user_id}:${session?.role}`;
  const [open, setOpen] = useState(false);
  const [policy, setPolicy] = useState<{ session: string; requiresScope: boolean } | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [policyRevision, setPolicyRevision] = useState(0);
  const [referenceDate, setReferenceDate] = useState("");
  const [threshold, setThreshold] = useState("");
  const [grace, setGrace] = useState("0");
  const [withInterest, setWithInterest] = useState(false);
  const [rate, setRate] = useState("");
  const [propertyId, setPropertyId] = useState("");
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [scopeValid, setScopeValid] = useState(true);
  const [preview, setPreview] = useState<{ key: string; input: DunningBatchInput; data: DunningBatchPreview } | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ session: string; body: DunningBatchCreate } | null>(null);
  const [created, setCreated] = useState<{ session: string; notices: DunningNotice[] } | null>(null);
  const busyRef = useRef(false);
  const generation = useRef(0);
  const live = useRef(true);
  const contextKey = JSON.stringify([sessionKey, periodStart, periodEnd, referenceDate, threshold, grace, withInterest, rate, propertyId, scopeId, scopeValid]);
  const activeContext = useRef(contextKey);
  const activeSession = useRef(sessionKey);
  useEffect(() => {
    activeContext.current = contextKey;
    activeSession.current = sessionKey;
    generation.current += 1;
    setPreview(null); setSelected([]); setConfirming(false); setError(null);
  }, [contextKey, sessionKey]);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; generation.current += 1; };
  }, []);

  useEffect(() => {
    if (!open || !canWrite) return;
    let current = true;
    setPolicy(null); setPolicyError(null);
    api.get<{ requires_accounting_scope: boolean }>("/api/fristen/context").then(result => {
      if (!current) return;
      if (typeof result.requires_accounting_scope !== "boolean") throw new Error("Berechtigungskontext ungültig.");
      setPolicy({ session: sessionKey, requiresScope: result.requires_accounting_scope });
    }).catch(reason => { if (current) setPolicyError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { current = false; };
  }, [open, canWrite, sessionKey, policyRevision]);

  function invalidate() {
    generation.current += 1;
    setPreview(null);
    setSelected([]);
    setConfirming(false);
    setError(null);
  }

  const normalizedThreshold = amount(threshold);
  const normalizedRate = rate.trim().replace(",", ".");
  const inputValid = validDate(periodStart) && validDate(periodEnd) && periodStart <= periodEnd
    && validDate(referenceDate) && normalizedThreshold !== null
    && /^\d+$/.test(grace) && Number.isSafeInteger(Number(grace))
    && (!withInterest || /^[+-]?(?:\d+(?:\.\d{1,4})?|\.\d{1,4})$/.test(normalizedRate))
    && policy?.session === sessionKey && (!policy.requiresScope || !!scopeId)
    && scopeValid && (!propertyId || !!scopeId);
  const currentPreview = preview?.key === contextKey ? preview : null;
  const chosen = currentPreview?.data.candidates.filter(row => selected.includes(row.lease_agreement_id)) ?? [];
  const principal = sumBatchAmounts(chosen.map(row => row.principal_amount));
  const total = sumBatchAmounts(chosen.map(row => row.total_amount));

  async function loadPreview() {
    if (!canWrite || !inputValid || busyRef.current || pending) return;
    const input: DunningBatchInput = {
      period_start: periodStart, period_end: periodEnd, reference_date: referenceDate,
      threshold: normalizedThreshold!, grace_days: Number(grace),
      base_interest_rate: withInterest ? normalizedRate : null,
      ...(scopeId ? { accounting_scope_id: scopeId } : {}),
    };
    invalidate(); setCreated(null);
    const revision = generation.current;
    const submittedContext = contextKey;
    busyRef.current = true; setBusy(true);
    try {
      const data = await previewDunningBatch(input);
      if (!live.current || generation.current !== revision || activeContext.current !== submittedContext) return;
      if (!Array.isArray(data.candidates) || typeof data.source_digest !== "string" || !data.source_digest) throw new Error("Unvollständige Mahnlaufvorschau. Bitte erneut prüfen.");
      // Fail closed on malformed monetary rows rather than confirming guessed totals.
      for (const row of data.candidates) sumBatchAmounts([row.principal_amount, row.fee_amount, row.interest_amount, row.total_amount]);
      if (new Set(data.candidates.map(row => row.lease_agreement_id)).size !== data.candidates.length) throw new Error("Doppelte Mietverträge in der Vorschau. Bitte erneut prüfen.");
      setPreview({ key: submittedContext, input, data });
    } catch (err) {
      if (live.current && generation.current === revision && activeContext.current === submittedContext) setError(err instanceof ApiError ? `HTTP ${err.status}: ${err.message}` : err instanceof Error ? err.message : String(err));
    } finally {
      busyRef.current = false;
      if (live.current) setBusy(false);
    }
  }

  async function createDrafts(retry = false) {
    if (!canWrite || busyRef.current || (pending && pending.session !== sessionKey)) return;
    if (!retry && (!inputValid || !currentPreview || chosen.length === 0 || pending)) return;
    const body = retry ? pending?.body : {
      ...currentPreview!.input, lease_agreement_ids: [...new Set(chosen.map(row => row.lease_agreement_id))],
      expected_source_digest: currentPreview!.data.source_digest, request_id: crypto.randomUUID(),
    };
    if (!body) return;
    const submittedSession = sessionKey;
    // Persist before dispatch: a lost response must never mint a second request key.
    setPending({ session: submittedSession, body });
    busyRef.current = true; setBusy(true); setError(null); setConfirming(false);
    try {
      const notices = await createDunningBatch(body);
      if (!live.current || activeSession.current !== submittedSession) return;
      if (!Array.isArray(notices) || !notices.length || notices.some(notice => !notice.id || notice.status !== "draft")) throw new Error("Unerwartete Anlageantwort; Entwurfsstatus nicht bestätigt.");
      setCreated({ session: submittedSession, notices }); setPending(null); setPreview(null); setSelected([]);
    } catch (err) {
      if (!live.current || activeSession.current !== submittedSession) return;
      const definitive = err instanceof ApiError && err.status >= 400 && err.status < 500 && err.status !== 408;
      if (definitive) { setPending(null); setPreview(null); setSelected([]); }
      setError(`${err instanceof ApiError ? `HTTP ${err.status}: ${err.message}` : err instanceof Error ? err.message : String(err)}${definitive ? " Bitte Eingaben/Berechtigungen prüfen und die Vorschau erneut laden." : " Ergebnis ungewiss. Nur den identischen Auftrag erneut versuchen; keine neuen Entwürfe starten."}`);
    } finally {
      busyRef.current = false;
      if (live.current) setBusy(false);
    }
  }

  if (!canWrite) return null;
  return <section className="card" aria-label="Mahnlauf">
    <button type="button" aria-expanded={open} onClick={() => { setOpen(!open); if (!pending) invalidate(); }}>
      {open ? "Mahnlauf schließen" : "Mahnlauf öffnen"}
    </button>
    {open && <div>
      <h2>Mahnlauf vorbereiten</h2>
      <p>Zeitraum: {periodStart} bis {periodEnd}. Der Stichtag ist ausdrücklich anzugeben.</p>
      <p>Es entstehen nur Mahnentwürfe. Originale bleiben unverändert; Entwürfe werden nicht ausgestellt, versendet oder als PDF erzeugt.</p>
      <p>Gebühr ohne Überschreibung (NULL): Es gelten die bestehenden Gebühren der jeweiligen Mahnstufe.</p>
      {policyError && <div role="alert">Berechtigungskontext nicht verfügbar: {policyError} <button type="button" onClick={() => setPolicyRevision(value => value + 1)}>Berechtigung erneut prüfen</button></div>}
      {!policy && !policyError && <p>Berechtigungskontext wird geprüft…</p>}
      {policy?.session === sessionKey && policy.requiresScope && !scopeId && <p>Für diesen Mahnlauf einen MIET-Abrechnungskreis mit Vorbereitungsrecht auswählen.</p>}
      <fieldset disabled={!!pending} style={{ border: 0, padding: 0 }}>
        <label>Stichtag *<input type="date" value={referenceDate} onChange={e => { invalidate(); setReferenceDate(e.target.value); }} /></label>
        <label>Mindestrückstand (EUR) *<input inputMode="decimal" value={threshold} onChange={e => { invalidate(); setThreshold(e.target.value); }} /></label>
        <label>Karenztage<input inputMode="numeric" value={grace} onChange={e => { invalidate(); setGrace(e.target.value); }} /></label>
        <label>Verzugszinsen<select value={withInterest ? "yes" : "no"} onChange={e => { invalidate(); setWithInterest(e.target.value === "yes"); }}><option value="no">Ohne Verzugszinsen</option><option value="yes">Mit explizitem Basiszinssatz</option></select></label>
        {withInterest && <label>Basiszinssatz (%)<input inputMode="decimal" value={rate} onChange={e => { invalidate(); setRate(e.target.value); }} /></label>}
        <label>Immobilie für Abrechnungskreis<select value={propertyId} onChange={e => { invalidate(); setPropertyId(e.target.value); setScopeId(null); setScopeValid(!e.target.value); }}><option value="">Ohne expliziten Abrechnungskreis</option>{properties.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        {propertyId && <AccountingScopeSelector key={propertyId} propertyId={propertyId} kind="MIET" capability="prepare" value={scopeId}
          onChange={id => { invalidate(); setScopeId(id); }} onValidityChange={setScopeValid} />}
      </fieldset>
      <button type="button" disabled={!inputValid || busy || !!pending} onClick={() => void loadPreview()}>Mahnlauf prüfen</button>
      {error && <p role="alert" className="error-text">{error}</p>}
      {pending && <div>
        <p>Gespeicherter Auftrag: {pending.body.lease_agreement_ids.length} Mahnentwürfe; Zeitraum {pending.body.period_start} bis {pending.body.period_end}, Stichtag {pending.body.reference_date}. Eingaben bleiben bis zur Klärung gesperrt.</p>
        <button type="button" disabled={busy || pending.session !== sessionKey} onClick={() => void createDrafts(true)}>Identischen Auftrag erneut versuchen</button>
        {pending.session !== sessionKey && <p role="alert">Der ungewisse Auftrag gehört zu einer anderen Sitzung. Bitte zuerst mit der ursprünglichen Sitzung klären.</p>}
      </div>}
      {currentPreview && !pending && <div>
        <p>{currentPreview.data.candidates.length} geeignete Rückstände oberhalb des Mindestbetrags.</p>
        <button type="button" onClick={() => setSelected(currentPreview.data.candidates.map(row => row.lease_agreement_id))}>Alle geeigneten Rückstände auswählen</button>
        <button type="button" onClick={() => setSelected([])}>Auswahl aufheben</button>
        <table className="data-table"><thead><tr><th>Auswahl / Mieter</th><th>Objekt / Einheit</th><th>Hauptforderung</th><th>Gebühr</th><th>Zinsen</th><th>Gesamt</th><th>Stufe</th></tr></thead><tbody>
          {currentPreview.data.candidates.map(row => <tr key={row.lease_agreement_id}>
            <td><label><input type="checkbox" checked={selected.includes(row.lease_agreement_id)} onChange={e => { setConfirming(false); setSelected(e.target.checked ? [...selected, row.lease_agreement_id] : selected.filter(id => id !== row.lease_agreement_id)); }} />{row.person_name} · Mietvertrag {row.lease_agreement_id}</label></td>
            <td>{row.property_name} / {row.unit_number}</td><td>{row.principal_amount} EUR</td><td>{row.fee_amount} EUR</td><td>{row.interest_amount} EUR</td><td>{row.total_amount} EUR</td><td>{row.level}</td>
          </tr>)}
        </tbody></table>
        <button type="button" disabled={!chosen.length || busy} onClick={() => setConfirming(true)}>Auswahl bestätigen</button>
        {confirming && chosen.length > 0 && <div role="dialog" aria-modal="true" aria-label="Mahnentwürfe anlegen">
          <h3>Mahnentwürfe anlegen</h3><p>{chosen.length} Mahnentwürfe: Hauptforderung {principal} EUR; Gesamtsumme {total} EUR.</p>
          <p>Nur Entwürfe, keine Ausstellung und kein Versand.</p>
          <button type="button" disabled={busy} onClick={() => void createDrafts()}>Mahnentwürfe anlegen</button>
          <button type="button" onClick={() => setConfirming(false)}>Abbrechen</button>
        </div>}
      </div>}
      {created?.session === sessionKey && <div role="status"><p>{created.notices.length} Mahnentwürfe angelegt.</p>{created.notices.map(notice => <p key={notice.id}><Link to={`/mahnungen/${encodeURIComponent(notice.id)}`}>Entwurf {notice.id} öffnen</Link></p>)}</div>}
    </div>}
  </section>;
}
