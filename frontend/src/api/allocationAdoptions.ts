import { api } from './client';
export interface AdoptionPreview {
  can_adopt: boolean; preview_digest: string | null; blocker: string | null;
  scope_id?: string; scope_label?: string; cost_count?: number; gross_amount?: string; disclosure?: string;
}
export interface AdoptionCommand { treatment_review_id: string; preview_digest: string; reason: string; costs_confirmed: true; whole_original_disclosure_reviewed: true }
export interface AllocationSet {
  id: string; kind: 'intake_reviewed' | 'proven_legacy'; source_document_id: string; primary_cost_id: string; property_id: string; scope_id: string;
  cost_count: number; financial_state: 'confirmed'; reason: string; confirmed_at: string; actor_id: string;
  costs: { cost_entry_id: string; position: number; gross_amount: string; vat_amount: string | null; tenant_amount: string | null }[];
}
export const allocationAdoptionsApi = {
  preview: (review: string, signal?: AbortSignal) => api.get<AdoptionPreview>(`/api/allocation-treatments/${review}/adoption-preview`, { signal }),
  result: (logical: string, signal?: AbortSignal) => api.get<{ item: AllocationSet | null }>(`/api/logical-documents/${logical}/allocation-adoptions`, { signal }),
  create: (logical: string, body: AdoptionCommand, key: string) => api.post<AllocationSet>(`/api/logical-documents/${logical}/allocation-adoptions`, body, {
    headers: { 'Idempotency-Key': key, 'If-Match': `"${body.treatment_review_id}"` } }),
};
