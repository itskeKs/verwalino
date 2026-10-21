import { useCallback, useEffect, useRef, useState } from "react";
import { downloadDatevCsv, getDatevSources, previewDatev, type DatevConfig, type DatevPreview, type DatevSources } from "../api/datev";
import type { Property } from "../api/types";
import { AccountingScopeSelector } from "./AccountingScopeSelector";

function message(error: unknown): string {
  return error instanceof Error ? error.message : "DATEV-Export fehlgeschlagen. Bitte erneut versuchen.";
}
function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || value.startsWith("0000")) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function DatevExport({ properties }: { properties: Property[] }) {
  const [propertyId, setPropertyId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [scopeValid, setScopeValid] = useState(true);
  const [sources, setSources] = useState<DatevSources | null>(null);
  const [chart, setChart] = useState<"" | DatevConfig["chart"]>("");
  const [fiscalStart, setFiscalStart] = useState("");
  const [advisor, setAdvisor] = useState("");
  const [client, setClient] = useState("");
  const [accountLength, setAccountLength] = useState("");
  const [banks, setBanks] = useState<DatevConfig["bank_accounts"]>({});
  const [payments, setPayments] = useState<DatevConfig["payment_accounts"]>({});
  const [categories, setCategories] = useState<DatevConfig["category_accounts"]>({});
  const [reviewed, setReviewed] = useState(false);
  const [result, setResult] = useState<{ config: DatevConfig; preview: DatevPreview } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"sources" | "preview" | "download" | null>(null);
  const lifecycle = useRef({ mounted: false, generation: 0, busy: false });
  useEffect(() => {
    const state = lifecycle.current;
    state.mounted = true;
    return () => { state.mounted = false; state.generation++; };
  }, []);

  const invalidate = useCallback((resetSources = false, resetMappings = false) => {
    lifecycle.current.generation++;
    lifecycle.current.busy = false;
    setBusy(null); setResult(null); setError(null);
    if (resetSources) setSources(null);
    if (resetSources || resetMappings) {
      setBanks({}); setPayments({}); setCategories({}); setReviewed(false);
    }
  }, []);
  const handleScopeValidity = useCallback((valid: boolean) => {
    setScopeValid(valid);
    if (!valid) invalidate(true);
  }, [invalidate]);
  function current(generation: number) {
    return lifecycle.current.mounted && lifecycle.current.generation === generation;
  }
  function finish(generation: number) {
    if (current(generation)) { lifecycle.current.busy = false; setBusy(null); }
  }
  const validSelection = properties.some(property => property.id === propertyId)
    && validDate(start) && validDate(end) && start <= end && scopeValid;
  const validLength = /^[4-8]$/.test(accountLength);
  const validAccount = (account: string | undefined) => !!account && validLength && new RegExp(`^\\d{${accountLength}}$`).test(account);
  const validConfig = validSelection && !!sources && !!chart && validDate(fiscalStart) && fiscalStart <= start
    && /^\d+$/.test(advisor) && /^\d+$/.test(client) && validLength && reviewed
    && sources.banks.every(bank => validAccount(banks[bank.id]))
    && sources.payment_types.every(type => validAccount(payments[type]))
    && sources.categories.every(category => validAccount(categories[category.id]?.account) && validAccount(categories[category.id]?.counter_account));

  async function loadSources() {
    if (!validSelection || lifecycle.current.busy) return;
    invalidate(true);
    const generation = lifecycle.current.generation;
    lifecycle.current.busy = true; setBusy("sources");
    try {
      const response = await getDatevSources({ property_id: propertyId, accounting_scope_id: scopeId, period_start: start, period_end: end });
      if (current(generation)) setSources(response);
    } catch (reason) { if (current(generation)) setError(message(reason)); }
    finally { finish(generation); }
  }
  async function showPreview() {
    if (!validConfig || !chart || lifecycle.current.busy) return;
    invalidate();
    const generation = lifecycle.current.generation;
    lifecycle.current.busy = true; setBusy("preview");
    const config: DatevConfig = {
      property_id: propertyId, accounting_scope_id: scopeId, period_start: start, period_end: end,
      fiscal_year_start: fiscalStart, chart, advisor_number: advisor, client_number: client,
      account_length: Number(accountLength), bank_accounts: banks, payment_accounts: payments, category_accounts: categories, gross_accounts_reviewed: true,
    };
    try {
      const preview = await previewDatev(config);
      if (current(generation)) setResult({ config, preview });
    } catch (reason) { if (current(generation)) setError(message(reason)); }
    finally { finish(generation); }
  }
  async function download() {
    if (!result || !validConfig || lifecycle.current.busy) return;
    const generation = lifecycle.current.generation;
    lifecycle.current.busy = true; setBusy("download"); setError(null);
    try {
      const blob = await downloadDatevCsv({ ...result.config, expected_source_digest: result.preview.source_digest });
      if (!current(generation)) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      try {
        anchor.href = url; anchor.download = "EXTF_Buchungsstapel.csv";
        document.body.append(anchor); anchor.click();
      } finally { anchor.remove(); URL.revokeObjectURL(url); }
    } catch (reason) { if (current(generation)) setError(message(reason)); }
    finally { finish(generation); }
  }

  return <section aria-label="DATEV-Export" className="mt-8 space-y-4">
    <h2>DATEV-Export (EXTF)</h2>
    <p>Der Export erfordert vollständigen Exportzugriff auf das ganze Objekt und den gewählten Zeitraum, nicht nur auf einzelne Einheiten. Bei gebundenen Objekten den genauen Abrechnungskreis wählen.</p>
    <p>Vollständiger aktueller Bestand im Zeitraum, kein Delta: Ein erneuter Import kann Doppelbuchungen verursachen. Eine heruntergeladene Datei ist kein Importerfolg in DATEV.</p>
    <p>Nur geprüfte Bruttobuchungen ohne Umsatzsteueraufteilung und ohne BU-Schlüssel. Keine automatische Umsatzsteuerunterstützung oder steuerliche Kontenempfehlung.</p>
    <p>Rechnungen gegen Verrechnungskonten, Bankbewegungen gegen Konten je Zahlungstyp zuordnen. Die expliziten Zuordnungen müssen doppelte Aufwendungen verhindern; Kosten nicht nochmals über die Bank als Aufwand buchen.</p>
    <form noValidate className="space-y-4" onSubmit={event => { event.preventDefault(); void showPreview(); }}>
      <div><label htmlFor="datev-property">DATEV-Objekt *</label><select id="datev-property" required value={propertyId} onChange={event => {
        invalidate(true); setPropertyId(event.target.value); setScopeId(null); setScopeValid(true);
      }}><option value="">Bitte wählen</option>{properties.map(property => <option key={property.id} value={property.id}>{property.name}</option>)}</select></div>
      <div><label htmlFor="datev-start">Zeitraum von *</label><input id="datev-start" type="date" required value={start} onChange={event => { invalidate(true); setStart(event.target.value); }} /></div>
      <div><label htmlFor="datev-end">Zeitraum bis *</label><input id="datev-end" type="date" required value={end} onChange={event => { invalidate(true); setEnd(event.target.value); }} /></div>
      {propertyId && <AccountingScopeSelector key={propertyId} propertyId={propertyId} capability="export" value={scopeId} onValidityChange={handleScopeValidity} onChange={value => { invalidate(true); setScopeId(value); }} />}
      <button type="button" disabled={!validSelection || !!busy} onClick={() => void loadSources()}>{busy === "sources" ? "DATEV-Quellen werden geladen…" : "DATEV-Quellen laden"}</button>
      <div><label htmlFor="datev-chart">Kontenrahmen *</label><select id="datev-chart" required value={chart} onChange={event => {
        invalidate(false, true); setChart(event.target.value as "" | DatevConfig["chart"]);
      }}><option value="">Bitte ausdrücklich wählen</option><option value="SKR03">SKR03</option><option value="SKR04">SKR04</option></select></div>
      <div><label htmlFor="datev-fiscal">Wirtschaftsjahresbeginn *</label><input id="datev-fiscal" type="date" required value={fiscalStart} onChange={event => { invalidate(); setFiscalStart(event.target.value); }} /></div>
      <div><label htmlFor="datev-advisor">Beraternummer *</label><input id="datev-advisor" inputMode="numeric" required value={advisor} onChange={event => { invalidate(); setAdvisor(event.target.value); }} /></div>
      <div><label htmlFor="datev-client">Mandantennummer *</label><input id="datev-client" inputMode="numeric" required value={client} onChange={event => { invalidate(); setClient(event.target.value); }} /></div>
      <div><label htmlFor="datev-length">Sachkontenlänge *</label><input id="datev-length" type="number" min="4" max="8" step="1" required value={accountLength} onChange={event => { invalidate(); setAccountLength(event.target.value); setReviewed(false); }} /></div>
      <p>Alle Konten ausdrücklich mit der gewählten Sachkontenlänge (4–8 Stellen) konfigurieren. Es gibt keine vorbelegten Konten; beim Wechsel des Kontenrahmens werden Zuordnungen und Bestätigung gelöscht.</p>
      {sources && <fieldset className="space-y-4"><legend>Kontenzuordnungen für die geladenen Quellen</legend>
        <p>Zahlungen: {sources.payment_total} EUR · Kosten: {sources.cost_total} EUR</p>
        {sources.banks.map(bank => <div key={bank.id}><label htmlFor={`datev-bank-${bank.id}`}>Bankkonto {bank.label} *</label><input id={`datev-bank-${bank.id}`} required inputMode="numeric" value={banks[bank.id] ?? ""} onChange={event => {
          invalidate(); setReviewed(false); setBanks({ ...banks, [bank.id]: event.target.value });
        }} /></div>)}
        {sources.payment_types.map(type => <div key={type}><label htmlFor={`datev-payment-${type}`}>Zahlungstyp {type} *</label><input id={`datev-payment-${type}`} required inputMode="numeric" value={payments[type] ?? ""} onChange={event => {
          invalidate(); setReviewed(false); setPayments({ ...payments, [type]: event.target.value });
        }} /></div>)}
        {sources.categories.map(category => <fieldset key={category.id}><legend>{category.label} ({category.code})</legend>
          {(["account", "counter_account"] as const).map(field => <div key={field}><label htmlFor={`datev-category-${category.id}-${field}`}>Kategorie {category.label} ({category.code}) {field === "account" ? "Konto" : "Gegenkonto"} *</label><input id={`datev-category-${category.id}-${field}`} required inputMode="numeric" value={categories[category.id]?.[field] ?? ""} onChange={event => {
            invalidate(); setReviewed(false); setCategories({ ...categories, [category.id]: { account: categories[category.id]?.account ?? "", counter_account: categories[category.id]?.counter_account ?? "", [field]: event.target.value } });
          }} /></div>)}
        </fieldset>)}
        {sources.banks.length === 0 && sources.categories.length === 0 && sources.payment_types.length === 0 && <p>Keine Quellen im gewählten Zeitraum.</p>}
      </fieldset>}
      <div><label><input type="checkbox" checked={reviewed} onChange={event => { invalidate(); setReviewed(event.target.checked); }} /> Ich habe alle Kontenzuordnungen fachlich geprüft, einschließlich Verrechnungskonten zur Vermeidung doppelter Aufwendungen. Die Zuordnung erfolgt nicht automatisch. Ich bestätige Bruttobuchungen auf Konten ohne Steuerautomatik, ohne Umsatzsteueraufteilung und ohne BU-Schlüssel.</label></div>
      <button type="submit" disabled={!validConfig || !!busy}>{busy === "preview" ? "DATEV-Vorschau wird erstellt…" : "DATEV-Vorschau erstellen"}</button>
    </form>
    {error && <p role="alert" className="error-text">{error}</p>}
    {result && <section aria-label="Geprüfter DATEV-Buchungsstapel">
      <h3>DATEV-Vorschau · {result.config.chart}</h3>
      <p>Zahlungen: {result.preview.payment_total} EUR · Kosten: {result.preview.cost_total} EUR</p>
      <div className="overflow-x-auto"><table aria-label="DATEV-Vorschau"><thead><tr><th>Quelle</th><th>Datum</th><th>Betrag (EUR)</th><th>Soll/Haben</th><th>Konto</th><th>Gegenkonto</th><th>Buchungstext</th></tr></thead><tbody>
        {result.preview.rows.map((row, index) => <tr key={`${row.source_type}-${row.source_id}-${index}`}><td>{row.source_type}</td><td>{row.day}</td><td>{row.amount}</td><td>{row.side}</td><td>{row.account}</td><td>{row.counter_account}</td><td>{row.text}</td></tr>)}
      </tbody></table></div>
      {result.preview.rows.length === 0 && <p>Keine Buchungen im Zeitraum.</p>}
      <button type="button" disabled={!!busy || !validConfig} onClick={() => void download()}>{busy === "download" ? "DATEV-Datei wird geladen…" : "DATEV-Datei herunterladen"}</button>
      <p>UTF-8 mit BOM · EXTF_Buchungsstapel.csv. Vor dem Import prüfen; ein erneuter Import birgt Doppelbuchungsrisiken.</p>
    </section>}
  </section>;
}
