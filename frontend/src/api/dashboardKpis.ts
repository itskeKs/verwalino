import { api } from "./client";

export interface DashboardKpiQuery {
  property_id: string;
  year: number;
  month: number;
  accounting_scope_id?: string;
}

export interface DashboardKpiResult {
  property_id: string;
  property_name: string;
  year: number;
  month: number;
  period_start: string;
  period_end: string;
  accounting_scope_id: string | null;
  occupancy_date: string;
  payment_basis: "assigned_rent_period";
  expected_rent: string;
  received_rent: string;
  arrears: string;
  arrears_count: number;
  unit_count: number;
  occupied_units: number;
  vacant_units: number;
  unassigned_rent_count: number;
  unassigned_rent_amount: string;
  footnote: string;
}

export async function getDashboardKpis(query: DashboardKpiQuery, signal?: AbortSignal): Promise<DashboardKpiResult> {
  if (!query.property_id || !Number.isInteger(query.year) || query.year < 1 || query.year > 9998
    || !Number.isInteger(query.month) || query.month < 1 || query.month > 12) {
    throw new Error("Objekt, Mietjahr (1–9998) und Mietmonat (1–12) müssen gültig sein.");
  }
  const params = new URLSearchParams({ property_id: query.property_id, year: String(query.year), month: String(query.month) });
  if (query.accounting_scope_id) params.set("accounting_scope_id", query.accounting_scope_id);
  return api.get<DashboardKpiResult>(`/api/reports/dashboard-kpis?${params}`, { signal });
}
