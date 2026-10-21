import { useEffect, useState } from "react";
import { getHeatingConsumption } from "../api/endpoints";
import type { HeatingBaseKey, HeatingConfig, HeatingConsumptionCandidate, HeatingSlice } from "../api/types";

type CandidateState = { rows: HeatingConsumptionCandidate[]; error: string | null; loading: boolean };
const decimal = (value: string) => value.trim().replace(",", ".");
const quantityValid = (value: string | null | undefined) => value != null && /^\d+(?:\.\d+)?$/.test(decimal(value));
export const sliceKey = (row: Pick<HeatingSlice, "unit_id" | "period_from" | "period_to">) => `${row.unit_id}/${row.period_from}/${row.period_to}`;
const requestKey = (runId: string, config: HeatingConfig) => JSON.stringify([runId, config.cost_entry_id, config.meter_type.trim(), config.base_key]);
export const fractionValid = (value: string) => /^0\.(?:[56]\d*|70*)$/.test(decimal(value));
export const seventy = (value: string) => /^0\.70*$/.test(decimal(value));

/** Candidate identity includes every GET parameter; obsolete responses cannot unlock actions. */
export function useHeatingCandidates(runId: string, configs: HeatingConfig[], enabled: boolean) {
  const [sources, setSources] = useState<Record<string, CandidateState>>({});
  const [retry, setRetry] = useState(0);
  const keys = JSON.stringify(enabled ? configs.filter(config => config.meter_type.trim()).map(config => requestKey(runId, config)) : []);
  useEffect(() => {
    let active = true;
    const requests: string[] = JSON.parse(keys);
    setSources(Object.fromEntries(requests.map(key => [key, { rows: [], error: null, loading: true }])));
    for (const key of requests) {
      const [id, costId, meterType, baseKey] = JSON.parse(key) as [string, string, string, HeatingBaseKey];
      getHeatingConsumption(id, costId, meterType, baseKey).then(rows => {
        if (active) setSources(previous => ({ ...previous, [key]: { rows, error: null, loading: false } }));
      }).catch((error: unknown) => {
        if (active) setSources(previous => ({ ...previous, [key]: { rows: [], error: error instanceof Error ? error.message : String(error), loading: false } }));
      });
    }
    return () => { active = false; };
  }, [keys, retry]);
  function sourceFor(config: HeatingConfig): CandidateState | undefined { return sources[requestKey(runId, config)]; }
  const blocked = configs.some(config => {
    const source = sourceFor(config);
    if (!config.meter_type.trim() || !fractionValid(config.consumption_fraction) ||
        (config.mandatory_seventy_percent && !seventy(config.consumption_fraction)) ||
        !source || source.loading || source.error || !source.rows.length) return true;
    if (config.slices.some(slice => !source.rows.some(row => sliceKey(row) === sliceKey(slice)))) return true;
    return source.rows.some(row => {
      const override = config.slices.find(slice => sliceKey(slice) === sliceKey(row));
      const base = override?.base_quantity ?? (config.base_key === "VOLUME" ? null : row.base_quantity);
      return !quantityValid(base) || (config.consumption_recorded && !quantityValid(override?.consumption ?? row.consumption));
    });
  });
  return { sourceFor, blocked, retry: () => setRetry(previous => previous + 1) };
}

export function heatingPayload(configs: HeatingConfig[]): HeatingConfig[] {
  return configs.map(config => ({ ...config, meter_type: config.meter_type.trim(), consumption_fraction: decimal(config.consumption_fraction),
    slices: config.slices.map(slice => ({ unit_id: slice.unit_id, period_from: slice.period_from, period_to: slice.period_to,
      ...(slice.base_quantity !== undefined ? { base_quantity: decimal(slice.base_quantity) } : {}),
      ...(config.consumption_recorded && slice.consumption !== undefined ? { consumption: decimal(slice.consumption) } : {}),
    })).filter(slice => slice.base_quantity !== undefined || slice.consumption !== undefined),
  }));
}
