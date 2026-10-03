import { api } from './client';
export interface AdoptionPreview {
  can_adopt: boolean; preview_digest: string | null; blocker: string | null;
  scope_id?: string; scope_label?: string; cost_count?: number; gross_amount?: string; disclosure?: string;
}
export interface AdoptionCommand { treatment_review_id: string; preview_digest: string; reason: string; costs_confirmed: true; whole_original_disclosure_reviewed: true }
export interface AllocationSet {
  transition_kind?: 'same_source' | 'source_replacement'; id: string; kind: 'intake_reviewed' | 'proven_legacy'; source_variant?: 'original' | 'visual'; source_document_id: string; primary_cost_id: string; property_id: string; scope_id: string;
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
  source_variant?: 'original' | 'visual'; visual_content_sha256?: string; source_document_id?: string; financial_state?: 'preview_only'; predecessor_id?: string; treatment_review_id?: string;
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

export interface VisualAdoptionPreview extends AdoptionPreview {
  source_variant: 'visual'; visual_content_sha256?: string; output_page_count?: number;
}
export interface VisualFinancialCommand {
  treatment_review_id: string; visual_content_sha256: string; preview_digest: string; reason: string;
  costs_confirmed: true; retained_visual_source_reviewed: true;
}
export interface VisualAdoptionCommand extends VisualFinancialCommand { disclosure_id: string }
export interface VisualCorrectionCommand extends VisualFinancialCommand { downstream_followups_reviewed: true }
export const visualAllocationApi = {
  preview: (review: string, disclosure: string, signal?: AbortSignal) => api.get<VisualAdoptionPreview>(
    `/api/allocation-treatments/${review}/visual-adoption-preview?disclosure_id=${encodeURIComponent(disclosure)}`, { signal }),
  create: (logical: string, body: VisualAdoptionCommand, key: string) => api.post<AllocationSet>(
    `/api/logical-documents/${logical}/visual-allocation-adoptions`, body,
    { headers: { 'Idempotency-Key': key, 'If-Match': `"${body.treatment_review_id}"` } }),
  correctionPreview: (id: string, review: string, signal?: AbortSignal) => api.get<CorrectionPreview>(
    `/api/allocation-sets/${id}/visual-correction-preview?treatment_review_id=${encodeURIComponent(review)}`, { signal }),
  correct: (id: string, body: VisualCorrectionCommand, key: string) => api.post<CorrectionResult>(
    `/api/allocation-sets/${id}/visual-corrections`, body,
    { headers: { 'Idempotency-Key': key, 'If-Match': `"${id}"` } }),
};

export interface ReplacementCandidate { id: string; source_document_id: string; document_number: string; seller_name: string; gross_amount: string; confirmed_at: string }
export const allocationReplacementsApi = {
  candidates: (review: string, after?: string, signal?: AbortSignal) => api.get<{ items: ReplacementCandidate[]; next_after: string | null }>(
    `/api/allocation-treatments/${review}/replacement-candidates${after ? `?after=${encodeURIComponent(after)}` : ''}`, { signal }),
  preview: (id: string, review: string, signal?: AbortSignal) => api.get<CorrectionPreview>(
    `/api/allocation-sets/${id}/replacement-preview?treatment_review_id=${encodeURIComponent(review)}`, { signal }),
  create: (id: string, body: CorrectionCommand, key: string) => api.post<CorrectionResult>(
    `/api/allocation-sets/${id}/source-replacements`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${id}"` } }),
};
