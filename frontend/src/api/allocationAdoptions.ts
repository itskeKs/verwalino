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

export interface CorrectionImpact {
  kind: 'settlement_run' | 'economic_plan'; id: string; run_type: string; status: 'draft' | 'finalized';
  period_start: string; period_end: string; superseded_by_id: string | null;
  effects: { exact_cost_consumption: boolean; potential_calculation_change: boolean; coverage_renewal: boolean; source_evidence_review: boolean };
  follow_up: 'refresh_and_revalidate' | 'review_finalized_successor';
}
export interface CorrectionPreview {
  can_review: boolean; preview_digest: string | null; blocker: string | null; impacts: CorrectionImpact[];
  financial_state?: 'preview_only'; predecessor_id?: string; treatment_review_id?: string;
  old?: CorrectionAmounts; proposed?: CorrectionAmounts;
}
interface CorrectionAmounts {
  gross_amount: string; service_period_from: string; service_period_to: string;
  costs: { position: number; gross_amount: string; vat_amount: string | null; tenant_amount: string | null }[];
}
export const correctionPreviewApi = {
  get: (setId: string, reviewId: string, signal?: AbortSignal) => api.get<CorrectionPreview>(
    `/api/allocation-sets/${setId}/correction-preview?treatment_review_id=${encodeURIComponent(reviewId)}`, { signal }),
};
