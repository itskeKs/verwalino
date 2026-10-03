import { api } from './client';
export interface ContextEvidence { kind: 'property_id' | 'meter_number' | 'service_address'; value: string; page_number: number; quotation: string; method: 'text_layer' | 'ocr' }
export interface ContextCandidate {
  scope_id: string; scope_label: string; scope_type: string; property_id: string; property_name: string;
  evidence: ContextEvidence[]; exclusions: string[]; requires_manual_review: true; meter_period_unverified: boolean;
}
export interface ContextCandidates {
  logical_document_id: string; partition_revision_id: string; extraction_revision_id: string;
  items: ContextCandidate[]; status: string; conflicting_properties: boolean; ambiguous_scopes: boolean;
  can_prepare: boolean; period_basis: 'unreviewed_extraction'; unmatched_evidence: ContextEvidence[];
}
export const contextCandidatesApi = {
  get: (logical: string, partition: string, extraction: string, signal?: AbortSignal) => api.get<ContextCandidates>(
    `/api/logical-documents/${logical}/assignment-candidates?partition_revision_id=${encodeURIComponent(partition)}&extraction_revision_id=${encodeURIComponent(extraction)}`, { signal }),
};
