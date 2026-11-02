import { useEffect, useRef, useState } from "react";
import { getPropertyYearReport, getPropertyYearReportCsv, getAnlageVSummary, getAnlageVSummaryCsv, anlageVCsvUrl, listProperties } from "../api/endpoints";
import type { AnlageVSummary, Property, PropertyYearReport, PropertyYearReportQuery } from "../api/types";
import { AccountingScopeSelector } from "../components/AccountingScopeSelector";
import { DatevExport } from "../components/DatevExport";
import { APPORTIONABILITY_LABELS, fromDbApportionability } from "../lib/costVocabulary";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";

const PAYMENT_TYPE_LABELS: Record<string, string> = {
  KALTMIETE: "Kaltmiete",
  NK_VORAUSZAHLUNG: "Betriebskosten-Vorauszahlung", HK_VORAUSZAHLUNG: "Heizkosten-Vorauszahlung",
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

function AnlageVPanel({ query }: { query: PropertyYearReportQuery }) {
  const [summary, setSummary] = useState<AnlageVSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const lifecycle = useRef({ mounted: false, downloading: false });
  useEffect(() => {
    const state = lifecycle.current;
    state.mounted = true;
    return () => { state.mounted = false; };
  }, []);
  useEffect(() => {
    let current = true;
    setError(null);
    getAnlageVSummary(query).then(value => { if (current) setSummary(value); })
      .catch(reason => { if (current) setError(message(reason)); });
    return () => { current = false; };
  }, [query, attempt]);

  async function download() {
    const state = lifecycle.current;
    if (state.downloading || !summary) return;
    state.downloading = true;
    setDownloading(true); setDownloadError(null);
    try {
      const blob = await getAnlageVSummaryCsv(query);
      if (!state.mounted) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      try {
        anchor.href = url;
        anchor.download = `anlage-v-${query.year}.csv`;
        document.body.append(anchor); anchor.click();
      } finally { anchor.remove(); URL.revokeObjectURL(url); }
    } catch (reason) {
      if (state.mounted) setDownloadError(message(reason));
    } finally {
      state.downloading = false;
      if (state.mounted) setDownloading(false);
    }
  }
  const euros = (value: string) => money(value).replace(/ EUR$/, " €");
  return <section aria-label="Anlage V" className="mt-6 min-w-0">
    <Card>
      <CardHeader>
        <CardTitle>Anlage V</CardTitle>
        <CardDescription>Zuordnungshilfe, keine Steuerberatung</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!summary && !error && <p role="status">Anlage V wird geladen…</p>}
        {error && <div role="alert" className="error-text">{error} <Button variant="outline" onClick={() => setAttempt(value => value + 1)}>Anlage V erneut laden</Button></div>}
        {summary && <>
          <p className="text-sm text-muted-foreground">{summary.footnote}</p>
          {summary.reconciliation.every(row => row.report_total === "0.00") && summary.unmapped.length === 0 && summary.lines.every(row => row.amount === "0.00") && <p>Keine Beträge im Zeitraum vorhanden.</p>}
          {(["Einnahmen", "Werbungskosten"] as const).map(section => <div key={section}>
            <h4 className="mb-2 font-semibold">{section}</h4>
            <div className="overflow-x-auto"><table aria-label={`Anlage V ${section}`} className="data-table">
              <thead><tr><th scope="col">Zuordnung</th><th scope="col">Betrag</th></tr></thead>
              <tbody>{summary.lines.filter(row => row.section === section).map(row => <tr key={row.key}>
                <th scope="row" className="text-left font-normal"><span className="text-sm font-normal normal-case tracking-normal text-foreground">{row.label}</span><p className="text-sm text-muted-foreground font-normal normal-case tracking-normal whitespace-normal">{row.note}</p></th>
                <td className="tabular-nums whitespace-nowrap">{euros(row.amount)}</td>
              </tr>)}</tbody>
            </table></div>
          </div>)}
          <div>
            <h4 className="mb-2 font-semibold">Nicht zugeordnet</h4>
            {summary.unmapped.length === 0 ? <p>Keine nicht zugeordneten Beträge vorhanden.</p> : <ul className="space-y-3">
              {summary.unmapped.map((row, index) => <li key={`${row.section}-${row.code}-${index}`} className="break-words">
                <span>{row.label}</span> <span className="text-sm">({row.code}, {row.section})</span> · <span className="tabular-nums whitespace-nowrap">{euros(row.amount)}</span>
                <p className="text-sm text-muted-foreground">{row.note}</p>
              </li>)}
            </ul>}
          </div>
          <div className="overflow-x-auto"><table aria-label="Abgleich mit der Jahresübersicht" className="data-table">
            <caption className="text-left font-semibold">Abgleich mit der Jahresübersicht</caption>
            <thead><tr><th scope="col">Bereich</th><th scope="col">Zugeordnet</th><th scope="col">Nicht zugeordnet</th><th scope="col">Jahresübersicht</th><th scope="col">Differenz</th></tr></thead>
            <tbody>{summary.reconciliation.map(row => <tr key={row.section}><th scope="row">{row.section}</th>
              {[row.mapped_total, row.unmapped_total, row.report_total, row.difference].map((value, index) => <td key={index} className="tabular-nums whitespace-nowrap">{euros(value)}</td>)}
            </tr>)}</tbody>
          </table></div>
          <a href={anlageVCsvUrl(query)} className="link" aria-disabled={downloading} onClick={event => { event.preventDefault(); void download(); }}>Anlage V als CSV herunterladen</a>
          {downloading && <p role="status">Anlage V CSV wird geladen…</p>}
          {downloadError && <p role="alert" className="error-text">{downloadError}</p>}
        </>}
      </CardContent>
    </Card>
  </section>;
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
      {result && <AnlageVPanel query={result.query} />}
    </section>}
    <section className="mt-8">
      <button type="button" aria-expanded={datevOpen} onClick={() => setDatevOpen(open => !open)}>{datevOpen ? "DATEV-Export schließen" : "DATEV-Export öffnen"}</button>
      {datevOpen && <DatevExport properties={properties} />}
    </section>
  </div>;
}
