import { useEffect, useRef, useState } from "react";
import { listSettlementPeriods } from "../api/endpoints";
import type { LeaseRecord, SettlementPeriod } from "../api/types";
import { formatDate } from "../lib/format";

/** Dates come from the backend's property-period function; free dates remain possible.
 * With keepFittingDates, dates that fit an offered period survive a property or year change;
 * dates that fit none are replaced by the offered period. */
export function SettlementPeriodSelect({ propertyId, year, start, end, lease, keepFittingDates, onSelect }: {
  propertyId: string;
  year?: number;
  start: string;
  end: string;
  lease?: Pick<LeaseRecord, "valid_from" | "valid_to">;
  keepFittingDates?: boolean;
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
      if (!offered) return;
      const { start: currentStart, end: currentEnd } = currentDates.current;
      if (!currentStart && !currentEnd) { select.current(offered); return; }
      if (!keepFittingDates) return;
      const fits = offeredPeriods.some(p => p.period_start <= currentStart && currentEnd <= p.period_end);
      if (!fits) select.current(offered);
    }).catch(() => { if (active) setError("Abrechnungszeiträume konnten nicht geladen werden. Bitte versuchen Sie es erneut."); });
    return () => { active = false; };
  }, [propertyId, year, leaseStart, leaseEnd, keepFittingDates]);
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
          ? ` · ${formatDate(p.period_start)} – ${formatDate(p.period_end)}` : ""}</option>)}
      </select>
    </label>
    {error && <p role="alert" className="error-text">{error}</p>}
  </>;
}
