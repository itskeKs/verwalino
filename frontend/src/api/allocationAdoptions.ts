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
  result: (logical: string, signal?: AbortSignal) => api.get<{ item: AllocationSet | null; correction?: CorrectionResult | null }>(`/api/logical-documents/${logical}/allocation-adoptions`, { signal }),
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

export interface CorrectionCommand extends AdoptionCommand { downstream_followups_reviewed: true }
export interface CorrectionResult {
  allocation: AllocationSet;
  receipt: { id: string; predecessor_id: string; successor_id: string; reason: string; actor_id: string; confirmed_at: string; preview_digest: string;
    impacts: { id: string; snapshot: CorrectionImpact }[] };
}
export const allocationCorrectionsApi = {
  detail: (id: string, signal?: AbortSignal) => api.get<CorrectionResult>(`/api/allocation-corrections/${id}`, { signal }),
  create: (predecessor: string, body: CorrectionCommand, key: string) => api.post<CorrectionResult>(`/api/allocation-sets/${predecessor}/corrections`, body, {
    headers: { 'Idempotency-Key': key, 'If-Match': `"${predecessor}"` } }),
};

export interface FollowupReceipt {
  id: string; impact_id: string; computation_id: string; allocation_head_id: string;
  kind: 'settlement_run' | 'economic_plan'; target_id: string; source_document_id: string;
  basis_digest: string; source_reviewed: boolean; reason: string; actor_id: string; completed_at: string;
}
export interface FollowupStatus {
  impact_id: string; status: 'open' | 'completed' | 'changed'; ready: boolean; blocker: string | null;
  preview_digest: string | null; kind: 'settlement_run' | 'economic_plan' | null; target_id: string | null;
  source_document_id: string | null; source_review_required: boolean; history: FollowupReceipt[];
}
export interface FollowupCommand { preview_digest: string; reason: string; source_reviewed: boolean; confirmed: true }
export const correctionFollowupsApi = {
  status: (id: string, signal?: AbortSignal) => api.get<FollowupStatus>(`/api/allocation-correction-impacts/${id}/follow-up`, { signal }),
  complete: (id: string, body: FollowupCommand, key: string) => api.post<FollowupReceipt>(`/api/allocation-correction-impacts/${id}/completions`, body, { headers: { 'Idempotency-Key': key } }),
};
