import { useEffect, useRef, useState } from "react";
import { listSettlementPeriods } from "../api/endpoints";
import type { SettlementPeriod } from "../api/types";

/** Dates come from the backend's property-period function; free dates remain possible. */
export function SettlementPeriodSelect({ propertyId, year, start, end, onSelect, freeDates = true }: {
  propertyId: string;
  year: number;
  start: string;
  end: string;
  freeDates?: boolean;
  onSelect: (period: SettlementPeriod) => void;
}) {
  const [periods, setPeriods] = useState<SettlementPeriod[]>([]);
  const [error, setError] = useState<string | null>(null);
  const select = useRef(onSelect);
  const currentDates = useRef({ start, end });
  useEffect(() => { select.current = onSelect; currentDates.current = { start, end }; });
  useEffect(() => {
    if (!propertyId) return;
    let active = true;
    listSettlementPeriods(propertyId, year).then(rows => {
      if (!active) return;
      setPeriods(rows);
      setError(null);
      const offered = rows.find(row => row.year === year) ?? rows[0];
      if (!currentDates.current.start && !currentDates.current.end && offered) select.current(offered);
    }).catch(() => { if (active) setError("Abrechnungszeiträume konnten nicht geladen werden. Bitte versuchen Sie es erneut."); });
    return () => { active = false; };
  }, [propertyId, year]);
  if (!propertyId) return null;
  const selected = periods.find(p => p.period_start === start && p.period_end === end);
  return <>
    <label>Abrechnungszeitraum
      <select aria-label="Abrechnungszeitraum" value={selected?.period_start ?? ""} onChange={event => {
        const period = periods.find(p => p.period_start === event.target.value);
        if (period) onSelect(period);
      }}>
        <option value="">{freeDates ? "Freier Zeitraum" : "Zeitraum wählen"}</option>
        {periods.map(p => <option key={p.year} value={p.period_start}>{p.label}</option>)}
      </select>
    </label>
    {error && <p role="alert" className="error-text">{error}</p>}
  </>;
}
