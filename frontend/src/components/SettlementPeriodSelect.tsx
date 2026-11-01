import { useEffect, useRef, useState } from "react";
import { listSettlementPeriods } from "../api/endpoints";
import type { LeaseRecord, SettlementPeriod } from "../api/types";

/** Dates come from the backend's property-period function; free dates remain possible. */
export function SettlementPeriodSelect({ propertyId, year, start, end, lease, onSelect }: {
  propertyId: string;
  year?: number;
  start: string;
  end: string;
  lease?: Pick<LeaseRecord, "valid_from" | "valid_to">;
  onSelect: (period: SettlementPeriod) => void;
}) {
  const [periods, setPeriods] = useState<SettlementPeriod[]>([]);
  const [error, setError] = useState<string | null>(null);
  const select = useRef(onSelect);
  const currentDates = useRef({ start, end });
  const leaseStart = lease?.valid_from;
  const leaseEnd = lease?.valid_to;
  useEffect(() => { select.current = onSelect; currentDates.current = { start, end }; });
  useEffect(() => {
    if (!propertyId) return;
    let active = true;
    listSettlementPeriods(propertyId, year).then(rows => {
      if (!active) return;
      const offeredPeriods = rows.map(row => ({ ...row,
        period_start: leaseStart && leaseStart > row.period_start ? leaseStart : row.period_start,
        period_end: leaseEnd && leaseEnd < row.period_end ? leaseEnd : row.period_end,
      })).filter(row => row.period_start <= row.period_end);
      setPeriods(offeredPeriods);
      setError(null);
      const offered = offeredPeriods.find(row => row.year === year) ?? offeredPeriods[0];
      if (!currentDates.current.start && !currentDates.current.end && offered) select.current(offered);
    }).catch(() => { if (active) setError("Abrechnungszeiträume konnten nicht geladen werden. Bitte versuchen Sie es erneut."); });
    return () => { active = false; };
  }, [propertyId, year, leaseStart, leaseEnd]);
  if (!propertyId) return null;
  const selected = periods.find(p => p.period_start === start && p.period_end === end);
  return <>
    <label>Abrechnungszeitraum
      <select aria-label="Abrechnungszeitraum" value={selected?.period_start ?? ""} onChange={event => {
        const period = periods.find(p => p.period_start === event.target.value);
        if (period) onSelect(period);
      }}>
        <option value="">Freier Zeitraum</option>
        {periods.map(p => <option key={p.year} value={p.period_start}>{p.label}{leaseStart
          ? ` · ${p.period_start.split("-").reverse().join(".")} – ${p.period_end.split("-").reverse().join(".")}` : ""}</option>)}
      </select>
    </label>
    {error && <p role="alert" className="error-text">{error}</p>}
  </>;
}
