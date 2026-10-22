import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { getDashboardKpis, type DashboardKpiResult } from "../api/dashboardKpis";
import { AccountingScopeSelector } from "./AccountingScopeSelector";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";

interface Props {
  properties: { id: string; name: string }[] | null;
  requiresScope: boolean | null;
  sessionKey: string;
}

const decimal = /^(-?)(\d+)\.(\d{2})$/;

// Keep cents exact even when the integer part exceeds JavaScript's safe integer range.
function euros(value: string): string {
  const [, sign, integer, cents] = decimal.exec(value)!;
  return `${sign}${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ".")},${cents} €`;
}

function dateLabel(iso: string): string {
  return iso.split("-").reverse().join(".");
}

export function DashboardKpis(props: Props) {
  return <KpiSelection key={`${props.sessionKey}:${props.requiresScope}`} {...props} />;
}

function KpiSelection({ properties, requiresScope }: Props) {
  const now = new Date();
  const [propertyId, setPropertyId] = useState("");
  const [month, setMonth] = useState(String(now.getMonth() + 1));
  const [year, setYear] = useState(String(now.getFullYear()));
  const [scopeId, setScopeId] = useState<string | null>(null);
  const [scopeValid, setScopeValid] = useState(true);
  const [result, setResult] = useState<DashboardKpiResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const locked = useRef(false);
  const validYear = /^\d+$/.test(year) && Number(year) >= 1 && Number(year) <= 9998;
  const validMonth = /^\d+$/.test(month) && Number(month) >= 1 && Number(month) <= 12;
  const propertyAvailable = !!properties?.some(property => property.id === propertyId);
  const ready = propertyAvailable && validYear && validMonth && requiresScope !== null
    && scopeValid && (!requiresScope || !!scopeId);

  const invalidate = useCallback(() => {
    ++generation.current;
    controller.current?.abort();
    locked.current = false;
    setResult(null); setError(null); setLoading(false);
  }, []);
  const checkScope = useCallback((valid: boolean) => {
    setScopeValid(valid);
    if (!valid) invalidate();
  }, [invalidate]);
  useEffect(() => () => { ++generation.current; controller.current?.abort(); }, []);
  useEffect(() => { if (!propertyAvailable) invalidate(); }, [propertyAvailable, invalidate]);

  async function load() {
    if (!ready || locked.current) return;
    locked.current = true;
    const request = ++generation.current;
    const abort = new AbortController();
    controller.current?.abort(); controller.current = abort;
    setResult(null); setError(null); setLoading(true);
    try {
      const data = await getDashboardKpis({ property_id: propertyId, year: Number(year), month: Number(month),
        ...(scopeId ? { accounting_scope_id: scopeId } : {}),
      }, abort.signal);
      if (request !== generation.current || abort.signal.aborted) return;
      if (data.payment_basis !== "assigned_rent_period" ||
        ![data.expected_rent, data.received_rent, data.arrears, data.unassigned_rent_amount].every(value => typeof value === "string" && decimal.test(value))) {
        throw new Error("Mietkennzahlen haben eine ungültige Zahlungsbasis oder ungültige Geldbeträge.");
      }
      setResult(data);
    } catch (reason) {
      if (request === generation.current && !abort.signal.aborted) {
        setError(reason instanceof ApiError ? `HTTP ${reason.status}: ${reason.message}` : reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (request === generation.current && !abort.signal.aborted) { locked.current = false; setLoading(false); }
    }
  }

  return <Card role="region" aria-label="Mietkennzahlen">
    <CardHeader><CardTitle>Mietkennzahlen</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <div className="form-grid">
        <label>Objekt für Mietkennzahlen
          <select value={propertyId} disabled={!properties} onChange={event => {
            invalidate(); setPropertyId(event.target.value); setScopeId(null); setScopeValid(true);
          }}><option value="">Objekt auswählen</option>{properties?.map(property => <option key={property.id} value={property.id}>{property.name}</option>)}</select>
        </label>
        <label>Mietmonat
          <select value={month} onChange={event => { invalidate(); setMonth(event.target.value); }}>
            {Array.from({ length: 12 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}
          </select>
        </label>
        <label>Mietjahr
          <input type="number" min="1" max="9998" step="1" value={year} aria-invalid={!validYear} onChange={event => { invalidate(); setYear(event.target.value); }} />
        </label>
        {propertyAvailable && <AccountingScopeSelector key={propertyId} propertyId={propertyId} capability="read" value={scopeId}
          onChange={id => { invalidate(); setScopeId(id); setScopeValid(true); }} onValidityChange={checkScope} />}
      </div>
      {!properties && <p>Objektliste nicht verfügbar. Bitte die Stammdaten erneut laden.</p>}
      {properties?.length === 0 && <p>Keine zugänglichen Objekte für Mietkennzahlen.</p>}
      {!propertyId && <p>Bitte ein Objekt für die Mietkennzahlen auswählen.</p>}
      {!validYear && <p>Mietjahr muss eine ganze Zahl von 1 bis 9998 sein.</p>}
      {!validMonth && <p>Mietmonat muss eine ganze Zahl von 1 bis 12 sein.</p>}
      {requiresScope === null && <p>Berechtigungskontext nicht verfügbar. Bitte die Fristen erneut laden.</p>}
      {requiresScope && !scopeId && <p>Für Mietkennzahlen einen Abrechnungskreis auswählen.</p>}
      {!!scopeId && !scopeValid && <p>Abrechnungskreis für Mietkennzahlen wird geprüft oder ist ungültig. Bitte neu wählen.</p>}
      <button disabled={!ready || loading} onClick={() => void load()}>Mietkennzahlen laden</button>
      {loading && <p role="status">Mietkennzahlen werden geladen…</p>}
      {error && <p role="alert">Mietkennzahlen nicht verfügbar: {error}</p>}
      {ready && result && <>
        <p>{result.property_name} · Mietzeitraum {dateLabel(result.period_start)} – {dateLabel(result.period_end)}</p>
        <div className="grid gap-4 sm:grid-cols-3">
          {[["Soll-Miete", result.expected_rent], ["Ist-Miete", result.received_rent], ["Mietrückstände", result.arrears]].map(([label, amount]) =>
            <div role="group" aria-label={label} key={label} className="rounded-lg border border-border p-4"><h3>{label}</h3><p className="text-2xl font-semibold tabular-nums">{euros(amount)}</p></div>)}
        </div>
        <p>Kaltmiete + Nebenkosten nach dem zugeordneten Mietmonat; nicht nach Wertstellung oder Buchungsdatum.</p>
        <p>Mietrückstände: positive Netto-Salden je Mietkonto seit Mietbeginn bis zum ausgewählten Monatsende, einschließlich beendeter Mietverhältnisse; keine Verrechnung von Guthaben zwischen Mietkonten.</p>
        <p>Anzahl positive Mietkonten: {result.arrears_count}</p>
        <p>Einheiten: {result.unit_count} · Belegt: {result.occupied_units} · Leer: {result.vacant_units} · Belegungsstichtag: {dateLabel(result.occupancy_date)}</p>
        <p>Leerstand: aktueller Einheitenbestand, geprüft zum ausgewählten Monatsende.</p>
        <p>{result.footnote}</p>
        {result.unassigned_rent_count > 0 && <p role="alert">{result.unassigned_rent_count} Mietzahlungen über {euros(result.unassigned_rent_amount)} sind keinem Mietmonat zugeordnet und nicht in der Ist-Miete enthalten. Bitte die Zuordnung prüfen.</p>}
      </>}
    </CardContent>
  </Card>;
}
