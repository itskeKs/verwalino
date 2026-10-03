import { api } from './client';

export interface ContextSnapshot {
  property: { id: string; name: string; street: string; zip_code: string; city: string };
  scope: { id: string; property_id: string; management_mandate_id: string | null; scope_type: string; label: string; valid_from: string; valid_to: string | null; whole_property: boolean };
}
export interface ContextPreview {
  logical_document_id: string; assignment_proposal_id: string; invoice_review_id: string | null; case_id: string | null;
  dependency_id: string | null; case_event_id: string | null; snapshot: ContextSnapshot; snapshot_digest: string;
  page_start: number | null; page_end: number | null; can_review: boolean;
}
export interface ContextCommand {
  assignment_proposal_id: string; invoice_review_id: string; snapshot_digest: string; expected_case_event_id: string | null;
  quotation: string; page_number: number | null; xml_path: string | null; comparison_note: string; context_reviewed: true;
}
export interface ContextDecision extends Omit<ContextCommand, 'expected_case_event_id'> {
  id: string; document_ref_id: string; logical_document_id: string; scope_id: string; property_id: string;
  case_id: string | null; dependency_id: string | null; previous_revision_id: string | null; revision_number: number;
  method: 'manual_document_comparison'; snapshot: ContextSnapshot; actor_id: string; created_at: string;
}
export interface ContextHistory { items: ContextDecision[]; next_before_revision: number | null }
export const contextDecisionsApi = {
  preview: (proposal: string, signal?: AbortSignal) => api.get<ContextPreview>(`/api/assignment-proposals/${proposal}/context-preview`, { signal }),
  history: (logical: string, signal?: AbortSignal) => api.get<ContextHistory>(`/api/logical-documents/${logical}/context-decisions?limit=20`, { signal }),
  create: (logical: string, body: ContextCommand, expected: string | null, key: string) => api.post<ContextDecision>(
    `/api/logical-documents/${logical}/context-decisions`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` } }),
};
