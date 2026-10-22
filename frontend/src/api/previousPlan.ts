import { api } from "./client";
import type { EconomicPlan } from "./types";

export interface PreviousPlanInput {
  property_id: string;
  plan_year: number;
  source_accounting_scope_id?: string | null;
  accounting_scope_id?: string | null;
}
export interface PreviousPlanCost {
  category_id: string;
  category_code: string;
  label: string;
  planned_amount: string;
  distribution_key: "MEA";
  cost_group: "UMLAGEFAEHIG" | "NICHT_UMLAGEFAEHIG";
}
export interface PreviousPlanPreview {
  source_year: number;
  plan_year: number;
  period_start: string;
  period_end: string;
  planned_costs: PreviousPlanCost[];
  reserve_contribution_total: string;
  source_digest: string;
  footnote: string;
}
export interface PreviousPlanCreate extends PreviousPlanInput {
  expected_source_digest: string;
  planned_costs: PreviousPlanCost[];
  reserve_contribution_total: string;
}
export function previewPreviousPlan(input: PreviousPlanInput, signal?: AbortSignal): Promise<PreviousPlanPreview> {
  return api.post("/api/economic-plans/previous/preview", input, { signal });
}
export function createPreviousPlan(input: PreviousPlanCreate): Promise<EconomicPlan> {
  return api.post("/api/economic-plans/previous", input);
}

/** Decimal strings only: never round sub-cent inputs or use binary money arithmetic. */
export function normalizePlanAmount(value: string): string | null {
  const text = value.trim().replace(",", ".");
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) return null;
  const [whole, fraction = ""] = text.split(".");
  return `${BigInt(whole)}.${fraction.padEnd(2, "0")}`;
}
export function sumPlanAmounts(values: string[]): string {
  const cents = values.reduce((total, value) => {
    const normalized = normalizePlanAmount(value);
    if (normalized === null) throw new Error("Beträge müssen nichtnegative Dezimalbeträge mit höchstens zwei Nachkommastellen sein.");
    const [whole, fraction] = normalized.split(".");
    return total + BigInt(whole) * 100n + BigInt(fraction);
  }, 0n);
  return `${cents / 100n}.${(cents % 100n).toString().padStart(2, "0")}`;
}
