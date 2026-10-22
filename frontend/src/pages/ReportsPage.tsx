import { useEffect, useRef, useState } from "react";
import { getPropertyYearReport, getPropertyYearReportCsv, listProperties } from "../api/endpoints";
import type { Property, PropertyYearReport, PropertyYearReportQuery } from "../api/types";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { DatevExport } from "../components/DatevExport";
import { APPORTIONABILITY_LABELS, fromDbApportionability } from "../lib/costVocabulary";

const PAYMENT_TYPE_LABELS: Record<string, string> = {
  KALTMIETE: "Kaltmiete",
  NK_VORAUSZAHLUNG: "NK-Vorauszahlung",
  KAUTION: "Kaution",
  HAUSGELD_VORSCHUSS: "Hausgeld-Vorschuss",
  RUECKLAGENANTEIL: "Ruecklagenanteil",
  SONDERZAHLUNG: "Sonder-/Nachzahlung",
};

// Aggregates may exceed both Numeric(14,2) and JavaScript's exact number range.
function money(value: string): string {
  const [integer, cents] = value.split(".");
  return `${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ".")},${cents} EUR`;
}
function message(error: unknown): string {
  return error instanceof Error ? error.message : "Die Übersicht konnte nicht geladen werden. Bitte erneut versuchen.";
}

export function ReportsPage() {
  const [properties, setProperties] = useState<Property[]>([]);
  const [datevOpen, setDatevOpen] = useState(false);
  const [lookupLoading, setLookupLoading] = useState(true);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [lookupAttempt, setLookupAttempt] = useState(0);
  const [propertyId, setPropertyId] = useState("");
  const [year, setYear] = useState("");
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [scopeValid, setScopeValid] = useState(true);
  const [result, setResult] = useState<{ query: PropertyYearReportQuery; report: PropertyYearReport } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [querying, setQuerying] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const lifecycle = useRef({ mounted: false, generation: 0, querying: false, downloading: false });

  useEffect(() => {
    const state = lifecycle.current;
    state.mounted = true;
    return () => { state.mounted = false; state.generation++; };
  }, []);
  useEffect(() => {
    let current = true;
    listProperties().then(items => {
      if (current) setProperties(items);
    }).catch(reason => {
      if (current) setLookupError(message(reason));
    }).finally(() => { if (current) setLookupLoading(false); });
    return () => { current = false; };
  }, [lookupAttempt]);

  function invalidate() {
    lifecycle.current.generation++;
    lifecycle.current.querying = false;
    lifecycle.current.downloading = false;
    setResult(null);
    setQuerying(false);
    setDownloading(false);
    setError(null);
    setDownloadError(null);
  }
  function isCurrent(generation: number) {
    return lifecycle.current.mounted && lifecycle.current.generation === generation;
  }
  const validYear = /^\d{1,4}$/.test(year) && Number(year) >= 1 && Number(year) <= 9999;
  const validSelection = properties.some(property => property.id === propertyId) && validYear && scopeValid;

  async function showReport() {
    if (!validSelection || lifecycle.current.querying) return;
    invalidate();
    const generation = lifecycle.current.generation;
    lifecycle.current.querying = true;
    setQuerying(true);
    const query: PropertyYearReportQuery = { property_id: propertyId, year: Number(year), ...(scopeId ? { accounting_scope_id: scopeId } : {}) };
    try {
      const report = await getPropertyYearReport(query);
      if (isCurrent(generation)) setResult({ query, report });
    } catch (reason) {
      if (isCurrent(generation)) setError(message(reason));
    } finally {
      if (isCurrent(generation)) { lifecycle.current.querying = false; setQuerying(false); }
    }
  }
  async function downloadCsv() {
    if (!result || lifecycle.current.downloading) return;
    const generation = lifecycle.current.generation;
    lifecycle.current.downloading = true;
    setDownloading(true);
    setDownloadError(null);
    try {
      const blob = await getPropertyYearReportCsv(result.query);
      if (!isCurrent(generation)) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      try {
        anchor.href = url;
        anchor.download = `objekt-jahresuebersicht-${result.query.year}.csv`;
        document.body.append(anchor);
        anchor.click();
      } finally {
        anchor.remove();
        URL.revokeObjectURL(url);
      }
    } catch (reason) {
      if (isCurrent(generation)) setDownloadError(message(reason));
    } finally {
      if (isCurrent(generation)) { lifecycle.current.downloading = false; setDownloading(false); }
    }
  }

  const report = result?.report;
  return <div>
    <h1>Berichte</h1>
    <h2>Objekt-Jahresübersicht</h2>
    {lookupError && <div role="alert">{lookupError} <button type="button" onClick={() => {
      setLookupError(null); setLookupLoading(true); setLookupAttempt(attempt => attempt + 1);
    }}>Objekte erneut laden</button></div>}
    <form noValidate onSubmit={event => { event.preventDefault(); void showReport(); }} className="mb-6 space-y-4">
      <div><label htmlFor="report-property">Objekt *</label>
        <select id="report-property" required value={propertyId} disabled={lookupLoading} onChange={event => {
          invalidate(); setPropertyId(event.target.value); setScopeId(null); setScopeValid(true);
        }}>
          <option value="">Bitte wählen</option>
          {properties.map(property => <option key={property.id} value={property.id}>{property.name}</option>)}
        </select>
      </div>
      <div><label htmlFor="report-year">Jahr *</label>
        <input id="report-year" type="number" min="1" max="9999" step="1" required value={year} onChange={event => { invalidate(); setYear(event.target.value); }} />
        <p>Ganzes Kalenderjahr von 1 bis 9999.</p>
      </div>
      {propertyId && <AccountingScopeSelector key={propertyId} propertyId={propertyId} capability="read" value={scopeId} onValidityChange={setScopeValid} onChange={value => { invalidate(); setScopeId(value); }} />}
      <p>Bei gebundenen Objekten bitte den genauen Abrechnungskreis wählen. Die Übersicht erfordert vollständigen Lesezugriff auf das Objekt und das Kalenderjahr.</p>
      <button type="submit" disabled={!validSelection || querying}>{querying ? "Übersicht wird geladen…" : "Übersicht anzeigen"}</button>
    </form>
    {error && <p role="alert" className="error-text">{error}</p>}
    {report && <section aria-label="Jahresübersicht">
      <h2>{report.property_name} · {report.year}</h2>
      <p>Zeitraum: {report.period_start} – {report.period_end}</p>
      <p>Einnahmen nach Wertstellungsdatum.</p>
      <p>Ausgaben nach Rechnungsdatum, bestätigte Bruttokosten.</p>
      <table aria-label="Einnahmen"><thead><tr><th scope="col">Zahlungstyp</th><th scope="col">Betrag (EUR)</th></tr></thead><tbody>
        {report.income.map(row => <tr key={row.payment_type}><td>{PAYMENT_TYPE_LABELS[row.payment_type] ?? `Unbekannter Zahlungstyp (${row.payment_type})`}</td><td>{money(row.amount)}</td></tr>)}
      </tbody></table>
      {report.income.length === 0 && <p>Keine Einnahmen im Zeitraum.</p>}
      <table aria-label="Ausgaben"><thead><tr><th scope="col">Kategorie</th><th scope="col">Code</th><th scope="col">Umlagefähigkeit</th><th scope="col">Betrag (EUR)</th></tr></thead><tbody>
        {report.expenses.map((row, index) => <tr key={`${row.category_id}-${row.tenant_apportionability}-${index}`}><td>{row.category_name}</td><td>{row.category_code}</td><td>{APPORTIONABILITY_LABELS[fromDbApportionability(row.tenant_apportionability)] ?? "Umlagefähigkeit unbekannt"}</td><td>{money(row.amount)}</td></tr>)}
      </tbody></table>
      {report.expenses.length === 0 && <p>Keine Ausgaben im Zeitraum.</p>}
      <dl><dt>Einnahmen</dt><dd>{money(report.income_total)}</dd><dt>Ausgaben</dt><dd>{money(report.expense_total)}</dd><dt>Saldo</dt><dd>{money(report.balance)}</dd></dl>
      <p>Teilweise umlagefaehige Kosten erfordern eine Prüfung; es erfolgt keine automatische Aufteilung.</p>
      <p>{report.footnote}</p>
      <button type="button" disabled={downloading} onClick={() => void downloadCsv()}>{downloading ? "CSV wird geladen…" : "CSV herunterladen"}</button>
      {downloadError && <p role="alert" className="error-text">{downloadError}</p>}
    </section>}
    <section className="mt-8">
      <button type="button" aria-expanded={datevOpen} onClick={() => setDatevOpen(open => !open)}>{datevOpen ? "DATEV-Export schließen" : "DATEV-Export öffnen"}</button>
      {datevOpen && <DatevExport properties={properties} />}
    </section>
  </div>;
}
