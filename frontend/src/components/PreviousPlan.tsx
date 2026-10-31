import { useContext, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { createPreviousPlan, normalizePlanAmount, previewPreviousPlan, sumPlanAmounts } from "../api/previousPlan";
import type { PreviousPlanInput, PreviousPlanPreview } from "../api/previousPlan";
import { AuthCtx } from "../auth/useAuth";
import { AccountingScopeSelector } from "./AccountingScopeSelector";

interface PropertyChoice { id: string; name: string; management_type: string }
function checkProposal(data: PreviousPlanPreview, year: number) {
  if (data.plan_year !== year || data.source_year !== year - 1
    || typeof data.period_start !== "string" || typeof data.period_end !== "string"
    || !data.period_start.startsWith(`${String(year - 1).padStart(4, "0")}-`)
    || !/^\d{4}-\d{2}-\d{2}$/.test(data.period_start) || !/^\d{4}-\d{2}-\d{2}$/.test(data.period_end)
    || data.period_end < data.period_start
    || typeof data.source_digest !== "string" || !data.source_digest || typeof data.footnote !== "string" || !Array.isArray(data.planned_costs)
    || typeof data.reserve_contribution_total !== "string" || normalizePlanAmount(data.reserve_contribution_total) === null) {
    throw new Error("Unvollständiger Vorjahresvorschlag. Bitte erneut prüfen.");
  }
  for (const row of data.planned_costs) {
    if (typeof row.category_id !== "string" || !row.category_id || typeof row.category_code !== "string" || !row.category_code
      || typeof row.label !== "string" || !row.label || row.distribution_key !== "MEA"
      || !["UMLAGEFAEHIG", "NICHT_UMLAGEFAEHIG"].includes(row.cost_group)
      || typeof row.planned_amount !== "string" || normalizePlanAmount(row.planned_amount) === null) {
      throw new Error("Ungültige Kategorien oder Beträge im Vorjahresvorschlag. Bitte erneut prüfen.");
    }
  }
  if (new Set(data.planned_costs.map(row => row.category_id)).size !== data.planned_costs.length) throw new Error("Doppelte Kategorien im Vorjahresvorschlag.");
}

export function PreviousPlan({ properties }: { properties: PropertyChoice[] }) {
  const auth = useContext(AuthCtx);
  const session = auth?.session;
  const canWrite = !!auth && !auth.loading && (session?.role === "OWNER_ADMIN" || session?.role === "CLERK");
  const sessionKey = JSON.stringify([session?.organization_id, session?.user_id, session?.role, auth?.loading]);
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [propertyId, setPropertyId] = useState("");
  const [year, setYear] = useState("");
  const [sourceScope, setSourceScope] = useState<string | null>(null);
  const [targetScope, setTargetScope] = useState<string | null>(null);
  const [sourceValid, setSourceValid] = useState(true);
  const [targetValid, setTargetValid] = useState(true);
  const [policy, setPolicy] = useState<{ key: string; required: boolean } | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [policyRevision, setPolicyRevision] = useState(0);
  const [proposal, setProposal] = useState<{ key: string; input: PreviousPlanInput; data: PreviousPlanPreview } | null>(null);
  const [amounts, setAmounts] = useState<string[]>([]);
  const [reserve, setReserve] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const busyRef = useRef(false);
  const generation = useRef(0);
  const live = useRef(false);
  const request = useRef<AbortController | null>(null);
  const choices = properties.filter(p => p.management_type === "WEG" || p.management_type === "MISCH");
  const contextKey = JSON.stringify([sessionKey, open, choices, propertyId, year, sourceScope, targetScope, sourceValid, targetValid, policy]);
  const activeContext = useRef(contextKey);
  const activeSession = useRef(sessionKey);
  useEffect(() => {
    activeContext.current = contextKey; activeSession.current = sessionKey;
    generation.current += 1; request.current?.abort();
    setProposal(null); setConfirming(false); setError(null);
  }, [contextKey, sessionKey]);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; generation.current += 1; request.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!open || !canWrite) return;
    const controller = new AbortController();
    setPolicy(null); setPolicyError(null);
    api.get<{ requires_accounting_scope: boolean }>("/api/fristen/context", { signal: controller.signal }).then(result => {
      if (controller.signal.aborted) return;
      if (typeof result.requires_accounting_scope !== "boolean") throw new Error("Berechtigungskontext ungültig.");
      setPolicy({ key: sessionKey, required: result.requires_accounting_scope });
    }).catch(failure => { if (!controller.signal.aborted) setPolicyError(failure instanceof Error ? failure.message : String(failure)); });
    return () => controller.abort();
  }, [open, canWrite, sessionKey, policyRevision]);

  function invalidate() {
    generation.current += 1; request.current?.abort(); setProposal(null); setConfirming(false); setError(null);
  }
  const planYear = /^\d+$/.test(year) ? Number(year) : NaN;
  const validYear = Number.isInteger(planYear) && planYear >= 2 && planYear <= 9998;
  const inputValid = canWrite && validYear && choices.some(p => p.id === propertyId)
    && policy?.key === sessionKey && (!policy.required || (!!sourceScope && !!targetScope)) && sourceValid && targetValid;
  const current = proposal?.key === contextKey ? proposal : null;
  const normalized = amounts.map(normalizePlanAmount);
  const normalizedReserve = normalizePlanAmount(reserve);
  const moneyValid = normalized.every(value => value !== null) && normalizedReserve !== null;
  const costTotal = current && moneyValid ? sumPlanAmounts(normalized as string[]) : null;
  const total = costTotal !== null ? sumPlanAmounts([costTotal, normalizedReserve!]) : null;

  async function preview() {
    if (!inputValid || busyRef.current || uncertain) return;
    invalidate(); const revision = generation.current; const key = contextKey;
    const controller = new AbortController(); request.current = controller;
    const input: PreviousPlanInput = { property_id: propertyId, plan_year: planYear,
      ...(sourceScope ? { source_accounting_scope_id: sourceScope } : {}), ...(targetScope ? { accounting_scope_id: targetScope } : {}) };
    busyRef.current = true; setBusy(true);
    try {
      const data = await previewPreviousPlan(input, controller.signal);
      if (!live.current || controller.signal.aborted || revision !== generation.current || key !== activeContext.current) return;
      checkProposal(data, planYear);
      setProposal({ key, input, data }); setAmounts(data.planned_costs.map(row => row.planned_amount)); setReserve(data.reserve_contribution_total);
    } catch (failure) {
      if (live.current && !controller.signal.aborted && revision === generation.current) setError(failure instanceof ApiError ? `HTTP ${failure.status}: ${failure.message}` : String(failure));
    } finally { busyRef.current = false; if (live.current) setBusy(false); }
  }
  async function create() {
    if (!canWrite || !inputValid || !current || !moneyValid || busyRef.current || uncertain || !confirming) return;
    const submittedSession = sessionKey;
    const submittedContext = contextKey;
    const revision = generation.current;
    busyRef.current = true; setBusy(true); setCreating(true); setConfirming(false); setError(null);
    // No idempotence contract: never retry a possibly committed request automatically.
    setUncertain(true);
    try {
      const created = await createPreviousPlan({ ...current.input, expected_source_digest: current.data.source_digest,
        planned_costs: current.data.planned_costs.map((row, i) => ({ ...row, planned_amount: normalized[i]! })), reserve_contribution_total: normalizedReserve! });
      if (!live.current || activeSession.current !== submittedSession || activeContext.current !== submittedContext || generation.current !== revision) return;
      if (!created.id) throw new Error("Unerwartete Anlageantwort.");
      navigate(`/wirtschaftsplaene/${encodeURIComponent(created.id)}`);
    } catch (failure) {
      if (!live.current || activeSession.current !== submittedSession || activeContext.current !== submittedContext || generation.current !== revision) return;
      const definitive = failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && failure.status !== 408;
      if (definitive) { setUncertain(false); setProposal(null); }
      setError(`${failure instanceof ApiError ? `HTTP ${failure.status}: ${failure.message}` : String(failure)} ${definitive
        ? "Bitte Eingaben und Berechtigungen prüfen und Vorjahres-Ist erneut prüfen."
        : "Ergebnis ungewiss. Vor einer weiteren Anlage zuerst die Planliste prüfen. Dieser Auftrag wird nicht wiederholt."}`);
    } finally { busyRef.current = false; if (live.current) { setBusy(false); setCreating(false); } }
  }
  if (!canWrite) return null;
  return <section className="card" aria-label="Plan aus Vorjahres-Ist">
    <button type="button" className="btn" aria-expanded={open} disabled={creating || uncertain} onClick={() => { invalidate(); setOpen(!open); }}>Plan aus Vorjahres-Ist</button>
    {open && <div>
      <h2>Plan aus Vorjahres-Ist vorbereiten</h2>
      <p>Nur ein Vorschlag: keine Berechnung, Bestätigung der Datengrundlage, Beschlussfassung oder Finalisierung. Rücklagenzuführung separat prüfen; sie ist keine Kostenposition.</p>
      {policyError && <p role="alert">Berechtigungskontext nicht verfügbar: {policyError} <button className="btn" disabled={uncertain || busy} onClick={() => { invalidate(); setPolicyRevision(value => value + 1); }}>Berechtigung erneut prüfen</button></p>}
      {!policy && !policyError && <p>Berechtigungskontext wird geprüft…</p>}
      {policy?.required && <p>Vorjahres-Lesekreis und neuer Vorbereitungskreis sind ausdrücklich und unabhängig auszuwählen.</p>}
      <fieldset disabled={creating || uncertain} style={{ border: 0, padding: 0 }}>
        <label>Objekt für Vorjahresplan<select value={propertyId} onChange={e => { invalidate(); setPropertyId(e.target.value); setSourceScope(null); setTargetScope(null); setSourceValid(true); setTargetValid(true); }}><option value="">Bitte wählen</option>{choices.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label>Zieljahr<input inputMode="numeric" value={year} placeholder="z. B. 2027" onChange={e => { invalidate(); setYear(e.target.value); }} /></label>
        <p>{validYear ? "Der neue Plan beginnt im Zieljahr. Der Abrechnungszeitraum richtet sich nach dem Objekt." : "Bitte geben Sie das Zieljahr als ganze Zahl von 2 bis 9998 an."}</p>
        {propertyId && <>
          <fieldset><legend>Vorjahres-Ist (Lesekreis)</legend><AccountingScopeSelector key={`read-${propertyId}-${sessionKey}`} propertyId={propertyId} kind="WEG" capability="read" value={sourceScope} onChange={id => { invalidate(); setSourceScope(id); }} onValidityChange={setSourceValid} /></fieldset>
          <fieldset><legend>Neuer Plan (Vorbereitungskreis)</legend><AccountingScopeSelector key={`prepare-${propertyId}-${sessionKey}`} propertyId={propertyId} kind="WEG" capability="prepare" value={targetScope} onChange={id => { invalidate(); setTargetScope(id); }} onValidityChange={setTargetValid} /></fieldset>
        </>}
        {policy && !policy.required && <p>Ohne Kreis nur im Legacy-Kontext; die Berechtigungsprüfung auf dem Server bleibt maßgeblich.</p>}
        <button type="button" className="btn" disabled={!inputValid || busy || uncertain} onClick={() => void preview()}>Vorjahres-Ist prüfen</button>
      </fieldset>
      {busy && <p role="status">{creating ? "Plan wird angelegt…" : "Vorjahres-Ist wird geprüft…"}</p>}
      {current && <section aria-label="Vorjahresvorschlag">
        <h3>Ist-Kosten {current.data.source_year}</h3><p>{current.data.period_start} bis {current.data.period_end}</p>
        <p>{current.data.footnote}</p>
        <fieldset disabled={busy || uncertain} style={{ border: 0, padding: 0 }}>
          <table className="data-table"><thead><tr><th>Kategorie</th><th>Verteilerschlüssel</th><th>Jahresbetrag (EUR)</th></tr></thead><tbody>{current.data.planned_costs.map((row, i) => <tr key={row.category_id}>
            <td>{row.category_code} {row.label}<div>{row.cost_group}</div></td><td>MEA</td>
            <td><input aria-label={`${row.label} – Jahresbetrag`} inputMode="decimal" value={amounts[i] ?? ""} onChange={e => { setConfirming(false); setAmounts(previous => previous.map((value, index) => index === i ? e.target.value : value)); }} /></td>
          </tr>)}</tbody></table>
          <label>Rücklagenzuführung – Vorschlag<input inputMode="decimal" value={reserve} onChange={e => { setConfirming(false); setReserve(e.target.value); }} /></label>
          {!moneyValid && <p role="alert">Beträge vollständig als nichtnegative Dezimalbeträge mit höchstens zwei Nachkommastellen angeben.</p>}
          {moneyValid && <><p>Kosten ohne Rücklage: {costTotal} EUR</p><p>Kosten und Rücklage: {total} EUR</p></>}
          <button type="button" className="btn btn--primary" disabled={!inputValid || !moneyValid || busy || uncertain} onClick={() => setConfirming(true)}>Plan aus Vorjahr anlegen</button>
        </fieldset>
      </section>}
      {confirming && current && <div role="dialog" aria-modal="true" aria-label="Plan aus Vorjahr anlegen" className="card">
        <h3>Plan aus Vorjahr anlegen</h3><p>Entwurf für {planYear} mit {total} EUR einschließlich Rücklage anlegen? Es erfolgt keine Berechnung oder Bestätigung.</p>
        <button className="btn btn--primary" disabled={busy} onClick={() => void create()}>Plan aus Vorjahr anlegen</button>
        <button className="btn" onClick={() => setConfirming(false)}>Abbrechen</button>
      </div>}
      {error && <p role="alert" className="error-text">{error}</p>}
      {uncertain && !creating && <p>Ergebnis ungewiss. Vor einer weiteren Anlage zuerst die <Link to="/wirtschaftsplaene">Planliste prüfen</Link>. Dieser Auftrag wird nicht wiederholt.</p>}
    </div>}
  </section>;
}
