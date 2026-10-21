import { api } from "./client";

export type SepaSequence = "FRST" | "RCUR";
export interface SepaCreditor {
  creditor_id: string;
  name: string;
  country: string;
  town: string;
  bank_authorized: boolean;
}
export interface SepaMandate {
  id: string;
  tenant_profile_id: string;
  reference: string;
  signed_on: string;
  sequence_type: SepaSequence;
  last_used_on: string | null;
  debtor_country: string;
  original_reference: string;
  revoked_at: string | null;
  source_current: boolean;
}
export interface SepaAccount {
  bank: { id: string; label: string; iban: string | null; bic: string | null; property_id: string };
  creditor: SepaCreditor | null;
  tenants: { tenant_profile_id: string; person_id: string; name: string; iban: string | null; city: string | null }[];
  mandates: SepaMandate[];
}
export interface SepaMandateCreate {
  tenant_profile_id: string;
  reference: string;
  signed_on: string;
  sequence_type: SepaSequence;
  last_used_on: string | null;
  debtor_country: string;
  confirmed_iban: string;
  original_reference: string;
  mandate_confirmed: true;
}
export interface SepaExportRequest {
  bank_account_id: string;
  accounting_scope_id: string | null;
  year: number;
  month: number;
  collection_date: string;
  message_id: string;
  pre_notification_on: string;
  notice_period_days: number;
  shorter_period_agreed: boolean;
  pre_notification_confirmed: true;
  bank_profile_confirmed: true;
}
export interface SepaPreview {
  rows: { tenant_profile_id: string; name: string; mandate_id: string; reference: string; amount: string; sequence_type: SepaSequence; lease_ids: string[] }[];
  total: string;
  source_digest: string;
}
const accountPath = (bankId: string) => `/api/payments/sepa/accounts/${encodeURIComponent(bankId)}`;
const mandatePath = (id: string) => `/api/payments/sepa/mandates/${encodeURIComponent(id)}`;
export function getSepaAccount(bankId: string): Promise<SepaAccount> {
  return api.get<SepaAccount>(accountPath(bankId));
}
// Configuration writes are followed by a fresh account GET, not optimistic consent data.
export async function saveSepaCreditor(bankId: string, creditor: SepaCreditor & { bank_authorized: true }): Promise<void> {
  await api.put(`${accountPath(bankId)}/creditor`, creditor);
}
export async function createSepaMandate(bankId: string, mandate: SepaMandateCreate): Promise<void> {
  await api.post(`${accountPath(bankId)}/mandates`, mandate);
}
export async function updateSepaMandate(id: string, history: { sequence_type: SepaSequence; last_used_on: string | null }): Promise<void> {
  await api.put(mandatePath(id), history);
}
export async function revokeSepaMandate(id: string): Promise<void> {
  await api.post(`${mandatePath(id)}/revoke`);
}
export function previewSepa(request: SepaExportRequest): Promise<SepaPreview> {
  return api.post<SepaPreview>("/api/payments/sepa/preview", request);
}
export function downloadSepaXml(request: SepaExportRequest & { expected_source_digest: string }): Promise<Blob> {
  return api.getBlob("/api/payments/sepa.xml", { method: "POST", body: JSON.stringify(request) });
}
