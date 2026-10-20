import type { CostEntry } from "../api/types";
import { fromDbDistributionKey } from "./costVocabulary";

/**
 * Determine which cost entries need structured input (EXTERNAL, CONSUMPTION,
 * PERSON_COUNT, MANUAL distribution keys).
 */
export function needsExternalInput(entry: CostEntry): boolean {
  return fromDbDistributionKey(entry.tenant_distribution_key ?? "") === "EXTERNAL";
}

export function needsManualInput(entry: CostEntry): boolean {
  return fromDbDistributionKey(entry.tenant_distribution_key ?? "") === "MANUAL";
}

export function needsConsumption(entries: CostEntry[]): boolean {
  return entries.some((e) => fromDbDistributionKey(e.tenant_distribution_key ?? "") === "CONSUMPTION");
}

export function needsPersonCount(entries: CostEntry[]): boolean {
  return entries.some((e) => fromDbDistributionKey(e.tenant_distribution_key ?? "") === "PERSON_COUNT");
}
