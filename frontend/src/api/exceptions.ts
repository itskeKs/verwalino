import { api } from './client';
import type { DuplicateComparison, DuplicateEvidence } from './duplicateComparisons';

export type ExceptionWorkflow = 'open' | 'snoozed' | 'completed';
export type ExceptionAction = 'claim' | 'release' | 'snooze' | 'reopen';
export interface ExceptionCase {
  id: string; document_ref_id: string; logical_document_id: string; extraction_revision_id: string;
  scope_id: string; scope_label: string; scope_type: string; cause: string; created_at: string; updated_at: string;
  revision_id: string; revision_number: number; resolution_state: 'unresolved' | 'resolved'; completion_id?: string | null; completion_changed?: boolean; workflow_state: ExceptionWorkflow;
  claimant_id: string | null; snoozed_until: string | null; dependency_count: number; duplicate_dependency_count?: number; can_work: boolean; context_decision_id?: string | null;
}
export interface ExceptionEvent {
  id: string; case_id: string; previous_event_id: string | null; revision_number: number;
  action: ExceptionAction | 'evidence_added' | 'context_reviewed' | 'duplicate_evidence_added' | 'duplicate_comparison_recorded' | 'completion_recorded'; resolution_state: 'unresolved' | 'resolved'; completion_id?: string | null; completion_changed?: boolean; workflow_state: ExceptionWorkflow;
  claimant_id: string | null; snoozed_until: string | null; reason: string; actor_id: string | null;
  dependency_id: string | null; duplicate_dependency_id?: string | null; duplicate_comparison_id?: string | null; context_decision_id?: string | null; created_at: string;
}
export interface ExceptionDependency {
  id: string; case_id: string; assignment_proposal_id: string; extraction_revision_id: string;
  cause: string; created_at: string; revision_number: number; proposal_revision_number: number; proposal_reason: string;
}
export interface DuplicateExceptionDependency {
  id: string; case_id: string; scope_id: string; anchor_review_id: string; reference_review_id: string;
  anchor_document_ref_id: string; anchor_logical_id: string; anchor_extraction_id: string;
  reference_document_ref_id: string; reference_logical_id: string; reference_extraction_id: string;
  anchor_seller_name: string; anchor_document_number: string; reference_seller_name: string; reference_document_number: string;
  evidence: DuplicateEvidence; created_at: string; revision_number: number; latest_exact_comparison: DuplicateComparison | null;
}
export interface ExceptionCommand { action: ExceptionAction; reason: string; snoozed_until: string | null }
export interface RevisionPage<T> { items: T[]; next_before_revision: number | null }
export interface CompletionMember {
  logical_document_id: string; document_ref_id: string; invoice_review_id: string; allocation_set_id: string | null;
  disposition: 'adopted_document' | 'covered_copy'; source_document_id: string | null; document_number?: string;
  gross_amount?: string; cost_count: number | null; primary_cost_id?: string;
}
export interface CompletionComparison {
  left_logical_id: string; right_logical_id: string; comparison_id: string | null;
  verdict: DuplicateComparison['verdict'] | null; subject_review_id: string | null; reference_review_id: string | null;
}
export interface CompletionDisposition {
  dependency_id: string; kind: 'context' | 'duplicate'; disposition: string; comparison_id?: string | null;
  context_decision_id?: string | null; allocation_set_id?: string | null;
}
export interface CompletionPreview {
  can_complete: boolean; expected_event_id: string; preview_digest: string | null; members: CompletionMember[];
  comparisons: CompletionComparison[]; dispositions: CompletionDisposition[]; blocker: string | null;
}
export interface CompletionCommand { preview_digest: string; reason: string; completion_reviewed: true }
export interface CompletionReceipt {
  id: string; case_id: string; expected_event_id: string; basis_digest: string; reason: string; created_at: string;
  members: CompletionMember[]; comparisons: CompletionComparison[]; dispositions: CompletionDisposition[];
}
export const exceptionApi = {
  completionPreview: (id: string, signal?: AbortSignal) => api.get<CompletionPreview>(`/api/exceptions/${id}/completion-preview`, { signal }),
  completion: (id: string, receipt: string, signal?: AbortSignal) => api.get<CompletionReceipt>(`/api/exceptions/${id}/completions/${receipt}`, { signal }),
  complete: (id: string, body: CompletionCommand, expected: string, key: string) => api.post<CompletionReceipt>(
    `/api/exceptions/${id}/completions`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected}"` } }),
  list: (workflow: ExceptionWorkflow | '', cursor?: string, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: '30' });
    if (workflow) params.set('workflow_state', workflow);
    if (cursor) params.set('cursor', cursor);
    return api.get<{ items: ExceptionCase[]; next_cursor: string | null }>('/api/exceptions?' + params, { signal });
  },
  get: (id: string, signal?: AbortSignal) => api.get<ExceptionCase>(`/api/exceptions/${id}`, { signal }),
  events: (id: string, before?: number, signal?: AbortSignal) => api.get<RevisionPage<ExceptionEvent>>(
    `/api/exceptions/${id}/events?limit=20${before ? '&before_revision=' + before : ''}`, { signal }),
  dependencies: (id: string, before?: number, signal?: AbortSignal) => api.get<RevisionPage<ExceptionDependency>>(
    `/api/exceptions/${id}/dependencies?limit=20${before ? '&before_revision=' + before : ''}`, { signal }),
  duplicateDependencies: (id: string, before?: number, signal?: AbortSignal) => api.get<RevisionPage<DuplicateExceptionDependency>>(
    `/api/exceptions/${id}/duplicate-dependencies?limit=20${before ? '&before_revision=' + before : ''}`, { signal }),
  append: (id: string, body: ExceptionCommand, expected: string, key: string) => api.post<ExceptionEvent>(
    `/api/exceptions/${id}/events`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected}"` } }),
};
export const exceptionCauses: Record<string, string> = {
  duplicate_candidate: 'Mögliche Dubletten vergleichen', scope_hint_conflict: 'Abrechnungskreis abgleichen', unsupported_scope_type: 'Abrechnungsart noch nicht unterstützt',
};
export const exceptionActions: Record<ExceptionEvent['action'], string> = {
  completion_recorded: 'Geprüften Abschluss dokumentiert', duplicate_evidence_added: 'Dublettenhinweis erkannt', duplicate_comparison_recorded: 'Menschlicher Belegvergleich verknüpft',
  evidence_added: 'Weiterer Zuordnungsvorschlag erfasst', claim: 'Bearbeitung beansprucht', release: 'Anspruch freigegeben', snooze: 'Zurückgestellt', reopen: 'Zur Bearbeitung wieder geöffnet', context_reviewed: 'Kontext einer einzelnen Zuordnung geprüft',
};
