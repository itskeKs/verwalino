import { api } from './client';
export const distributionKeys = ['MEA', 'WOHNFLAECHE', 'BEHEIZTE_FLAECHE', 'EINHEIT', 'PERSONENZAHL', 'VERBRAUCH', 'EXTERNE_ABRECHNUNG', 'MANUELLER_BETRAG'] as const;
export type DistributionKey = typeof distributionKeys[number];
export interface TreatmentLine {
  allocation_line_id: string;
  expense_intent: 'operating_expense' | 'reserve_related' | 'capitalization_related' | 'unresolved';
  tax_intent: 'gross_without_input_vat' | 'input_vat_requested' | 'unresolved';
  tenant_treatment: 'full' | 'partial' | 'none' | 'unresolved'; tenant_amount: string | null;
  tenant_distribution_key: DistributionKey | null; owner_distribution_key: DistributionKey | 'unresolved' | 'not_applicable'; documentary_basis: string;
}
export interface TreatmentCommand { allocation_proposal_id: string; preview_digest: string; reason: string; treatment_reviewed: true; lines: TreatmentLine[] }
export interface TreatmentSnapshot {
  allocation_proposal_id: string; scope_type: 'WEG' | 'MIET'; lines: { allocation_line_id: string; position: number; gross_amount: string; vat_amount: string | null;
    category: { id: string; plan_version: number; code: string; label: string; is_active: boolean; cost_group: string; reserve_relevant: boolean;
      tenant_apportionability_default: string; owner_billing_relevant_default: boolean; owner_distribution_key_default: string | null; tenant_distribution_key_default: string | null } }[];
}
export interface TreatmentPreview { snapshot: TreatmentSnapshot; snapshot_digest: string; can_review: boolean }
export interface TreatmentReview { id: string; logical_document_id: string; allocation_proposal_id: string; previous_revision_id: string | null; revision_number: number;
  financial_state: 'review_only'; marker_policy: 'allocation-treatment-v1'; snapshot: TreatmentSnapshot; snapshot_digest: string; reason: string; treatment_reviewed: true; actor_id: string; created_at: string;
  lines: (TreatmentLine & { id: string; markers: string[] })[];
}
export interface TreatmentHistory { items: TreatmentReview[]; next_before_revision: number | null }
export const allocationTreatmentsApi = {
  preview: (allocation: string, signal?: AbortSignal) => api.get<TreatmentPreview>(`/api/allocation-proposals/${allocation}/treatment-preview`, { signal }),
  history: (logical: string, before?: number, signal?: AbortSignal) => api.get<TreatmentHistory>(`/api/logical-documents/${logical}/allocation-treatments?limit=20${before ? `&before_revision=${before}` : ''}`, { signal }),
  create: (logical: string, body: TreatmentCommand, expected: string | null, key: string) => api.post<TreatmentReview>(`/api/logical-documents/${logical}/allocation-treatments`, body, {
    headers: { 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` } }),
};
