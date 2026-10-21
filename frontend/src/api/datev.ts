import { api } from "./client";

export interface DatevSelection {
  property_id: string;
  accounting_scope_id?: string | null;
  period_start: string;
  period_end: string;
}
export interface DatevSources {
  banks: { id: string; label: string }[];
  categories: { id: string; code: string; label: string }[];
  payment_types: string[];
  payment_total: string;
  cost_total: string;
}
export interface DatevConfig extends DatevSelection {
  accounting_scope_id: string | null;
  fiscal_year_start: string;
  chart: "SKR03" | "SKR04";
  advisor_number: string;
  client_number: string;
  account_length: number;
  bank_accounts: Record<string, string>;
  payment_accounts: Record<string, string>;
  category_accounts: Record<string, { account: string; counter_account: string }>;
  gross_accounts_reviewed: true;
}
export interface DatevPreview {
  rows: { source_type: string; source_id: string; day: string; amount: string; side: string; account: string; counter_account: string; text: string }[];
  payment_total: string;
  cost_total: string;
  source_digest: string;
}
export function getDatevSources(selection: DatevSelection): Promise<DatevSources> {
  const query = new URLSearchParams({ property_id: selection.property_id, period_start: selection.period_start, period_end: selection.period_end });
  if (selection.accounting_scope_id) query.set("accounting_scope_id", selection.accounting_scope_id);
  return api.get<DatevSources>(`/api/reports/datev/sources?${query}`);
}
export function previewDatev(config: DatevConfig): Promise<DatevPreview> {
  return api.post<DatevPreview>("/api/reports/datev/preview", config);
}
export function downloadDatevCsv(config: DatevConfig & { expected_source_digest: string }): Promise<Blob> {
  return api.getBlob("/api/reports/datev.csv", { method: "POST", body: JSON.stringify(config) });
}
