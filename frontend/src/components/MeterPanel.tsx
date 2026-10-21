import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { createMeter, createMeterReading, listMeterReadings, listMeters, updateMeter } from "../api/endpoints";
import type { Meter, MeterReading } from "../api/types";
import { ApiError } from "../api/client";

interface MeterPanelProps { propertyId: string; unitId?: string; canWrite: boolean }
type FormKind = "create" | "edit" | "reading" | null;
const emptyMeter = { number: "", type: "KALTWASSER", location: "" };
const emptyReading = { date: "", value: "", period: "" };

function canonicalValue(input: string): string | null {
  const match = /^(\d+)(?:[.,](\d{1,4}))?$/.exec(input.trim());
  if (!match) return null;
  const whole = match[1].replace(/^0+(?=\d)/, "");
  if (whole.length > 10) return null;
  return `${whole}.${(match[2] ?? "").padEnd(4, "0")}`;
}
function scaled(input: string): bigint | null {
  const value = canonicalValue(input);
  return value === null ? null : BigInt(value.replace(".", ""));
}
function display(input: string): string {
  return canonicalValue(input)?.replace(".", ",") ?? "Ablesungen prüfen";
}
function ordered(rows: MeterReading[]): MeterReading[] {
  return [...rows].sort((a, b) => a.reading_date.localeCompare(b.reading_date) || a.id.localeCompare(b.id));
}
function consumption(rows: MeterReading[]): string | null {
  if (rows.length < 2) return null;
  const start = rows[rows.length - 2], end = rows[rows.length - 1];
  const a = scaled(start.value), b = scaled(end.value);
  if (start.reading_date === end.reading_date || a === null || b === null || b < a
    || rows.filter(row => row.reading_date === start.reading_date || row.reading_date === end.reading_date).length !== 2) {
    return "Ablesungen prüfen: Rückgang oder mehrdeutige Ablesetage; keine automatische Verbrauchsdifferenz.";
  }
  const delta = b - a;
  return `Verbrauch: ${delta / 10000n},${String(delta % 10000n).padStart(4, "0")} (${start.reading_date} bis ${end.reading_date})`;
}
function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}
function failure(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Verbindung fehlgeschlagen. Bitte erneut versuchen.";
}

export function MeterPanel(props: MeterPanelProps) {
  return <MeterPanelContent key={`${props.propertyId}/${props.unitId ?? ""}`} {...props} />;
}

function MeterPanelContent({ propertyId, unitId, canWrite }: MeterPanelProps) {
  const [meters, setMeters] = useState<Meter[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [listVersion, setListVersion] = useState(0);
  const [selected, setSelected] = useState<Meter | null>(null);
  const [readings, setReadings] = useState<MeterReading[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyVersion, setHistoryVersion] = useState(0);
  const [form, setForm] = useState<FormKind>(null);
  const [editing, setEditing] = useState<Meter | null>(null);
  const [meterForm, setMeterForm] = useState(emptyMeter);
  const [readingForm, setReadingForm] = useState(emptyReading);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const mutationLock = useRef(false);
  const alive = useRef(false);
  const selectedId = selected?.id;

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  useEffect(() => {
    let active = true;
    listMeters(propertyId, unitId).then(rows => {
      if (active) { setMeters(rows); setListError(null); }
    }).catch(error => {
      if (active) setListError(failure(error));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [propertyId, unitId, listVersion]);
  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    listMeterReadings(selectedId).then(rows => {
      if (active) { setReadings(ordered(rows)); setHistoryError(null); }
    }).catch(error => {
      if (active) setHistoryError(failure(error));
    }).finally(() => { if (active) setHistoryLoading(false); });
    return () => { active = false; };
  }, [selectedId, historyVersion]);

  function choose(meter: Meter) {
    if (mutationLock.current) return;
    setSelected(meter); setReadings([]); setHistoryError(null); setHistoryLoading(true);
    setHistoryVersion(value => value + 1); setForm(null); setFormError(null);
  }
  function open(kind: Exclude<FormKind, null>, meter?: Meter) {
    if (!canWrite || mutationLock.current) return;
    setForm(kind); setFormError(null); setEditing(meter ?? null);
    setMeterForm(meter ? { number: meter.meter_number, type: meter.meter_type, location: meter.location } : emptyMeter);
    setReadingForm(emptyReading);
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canWrite || mutationLock.current || !form) return;
    const value = canonicalValue(readingForm.value);
    if (form === "reading") {
      if (!selected || !validDate(readingForm.date) || value === null || readingForm.period.length > 50) {
        setFormError("Datum und nichtnegativen Zählerstand mit höchstens zehn Ganzzahl- und vier Nachkommastellen prüfen; Zeitraum maximal 50 Zeichen.");
        return;
      }
    } else if (!meterForm.number.trim() || meterForm.number.trim().length > 100
      || !meterForm.type.trim() || meterForm.type.trim().length > 50 || meterForm.location.length > 255) {
      setFormError("Zählernummer (maximal 100), Zählertyp (maximal 50) und Standort (maximal 255 Zeichen) prüfen.");
      return;
    }
    mutationLock.current = true; setSaving(true); setFormError(null);
    try {
      if (form === "reading" && selected && value !== null) {
        const row = await createMeterReading(selected.id, {
          reading_date: readingForm.date, value, period: readingForm.period.trim() || null,
        });
        if (!alive.current) return;
        setReadings(current => ordered([...current, row]));
      } else {
        const descriptors = { meter_number: meterForm.number.trim(), meter_type: meterForm.type.trim(), location: meterForm.location.trim() };
        const row = form === "edit" && editing
          ? await updateMeter(editing.id, descriptors)
          : await createMeter({ property_id: propertyId, unit_id: unitId ?? null, ...descriptors });
        if (!alive.current) return;
        setMeters(current => current.some(item => item.id === row.id)
          ? current.map(item => item.id === row.id ? row : item) : [...current, row]);
        setSelected(current => current?.id === row.id ? row : current);
      }
      setForm(null);
    } catch (error) {
      if (alive.current) setFormError(failure(error));
    } finally {
      mutationLock.current = false;
      if (alive.current) setSaving(false);
    }
  }
  const difference = consumption(readings);
  return <section aria-label="Zähler" style={{ marginBlock: "2rem" }}>
    <h2>Zähler</h2>
    {loading && <p>Zähler werden geladen …</p>}
    {listError && <div role="alert">{listError} <button type="button" className="btn" onClick={() => {
      setLoading(true); setListError(null); setListVersion(value => value + 1);
    }}>Erneut versuchen</button></div>}
    {!loading && !listError && meters.length === 0 && <p>Keine Zähler vorhanden.</p>}
    {!listError && meters.length > 0 && <table className="data-table" aria-label="Zählerliste">
      <thead><tr><th>Zählernummer</th><th>Typ</th><th>Standort</th><th>Aktionen</th></tr></thead>
      <tbody>{meters.map(meter => <tr key={meter.id}>
        <td>{meter.meter_number}</td><td>{meter.meter_type}</td><td>{meter.location || "–"}</td>
        <td><button type="button" className="btn" disabled={saving} onClick={() => choose(meter)}>Verlauf anzeigen</button>
          {canWrite && <button type="button" className="btn" disabled={saving} onClick={() => open("edit", meter)}>Zähler bearbeiten</button>}</td>
      </tr>)}</tbody>
    </table>}
    {canWrite && <button type="button" className="btn" disabled={loading || saving || Boolean(listError)} onClick={() => open("create")}>Zähler anlegen</button>}
    {selected && <section aria-label={`Verlauf: ${selected.meter_number}`}>
      <h3>Verlauf: {selected.meter_number}</h3>
      {historyLoading && <p>Ablesungen werden geladen …</p>}
      {historyError && <div role="alert">{historyError} <button type="button" className="btn" disabled={saving} onClick={() => choose(selected)}>Verlauf erneut laden</button></div>}
      {!historyLoading && !historyError && <>
        {readings.length === 0 ? <p>Keine Ablesungen vorhanden.</p> : <table className="data-table" aria-label="Ablesungen">
          <thead><tr><th>Ablesedatum</th><th>Zählerstand</th><th>Zeitraum</th></tr></thead>
          <tbody>{readings.map(row => <tr key={row.id}><td>{row.reading_date}</td><td>{display(row.value)}</td><td>{row.period ?? "–"}</td></tr>)}</tbody>
        </table>}
        {difference && <p role="status" aria-label="Verbrauch">{difference}</p>}
        {canWrite && <button type="button" className="btn" disabled={saving} onClick={() => open("reading")}>Stand erfassen</button>}
      </>}
    </section>}
    {form && canWrite && <form onSubmit={submit} noValidate aria-label={form === "reading" ? "Stand erfassen" : "Zählerdaten"}>
      <h3>{form === "reading" ? "Stand erfassen" : form === "create" ? "Zähler anlegen" : "Zähler bearbeiten"}</h3>
      {formError && <p role="alert">{formError}</p>}
      <fieldset disabled={saving} style={{ border: 0, padding: 0 }}>
        {form === "reading" ? <>
          <label>Ablesedatum *<input type="date" required value={readingForm.date} onChange={event => setReadingForm({ ...readingForm, date: event.target.value })} /></label>
          <label>Zählerstand *<input type="text" inputMode="decimal" required value={readingForm.value} onChange={event => setReadingForm({ ...readingForm, value: event.target.value })} /></label>
          <label>Zeitraum<input type="text" maxLength={50} value={readingForm.period} onChange={event => setReadingForm({ ...readingForm, period: event.target.value })} /></label>
        </> : <>
          <label>Zählernummer *<input type="text" required maxLength={100} value={meterForm.number} onChange={event => setMeterForm({ ...meterForm, number: event.target.value })} /></label>
          <label>Zählertyp *<input type="text" required maxLength={50} value={meterForm.type} onChange={event => setMeterForm({ ...meterForm, type: event.target.value })} /></label>
          <label>Standort<input type="text" maxLength={255} value={meterForm.location} onChange={event => setMeterForm({ ...meterForm, location: event.target.value })} /></label>
        </>}
        <div><button type="submit" className="btn btn--primary">{form === "reading" ? "Stand speichern" : "Zähler speichern"}</button>
          <button type="button" className="btn" onClick={() => { if (!mutationLock.current) { setForm(null); setFormError(null); } }}>Abbrechen</button></div>
      </fieldset>
    </form>}
  </section>;
}
