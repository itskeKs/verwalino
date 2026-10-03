import { api } from './client';

export type ExceptionWorkflow = 'open' | 'snoozed';
export type ExceptionAction = 'claim' | 'release' | 'snooze' | 'reopen';
export interface ExceptionCase {
  id: string; document_ref_id: string; logical_document_id: string; extraction_revision_id: string;
  scope_id: string; scope_label: string; scope_type: string; cause: string; created_at: string; updated_at: string;
  revision_id: string; revision_number: number; resolution_state: 'unresolved'; workflow_state: ExceptionWorkflow;
  claimant_id: string | null; snoozed_until: string | null; dependency_count: number; can_work: boolean;
}
export interface ExceptionEvent {
  id: string; case_id: string; previous_event_id: string | null; revision_number: number;
  action: ExceptionAction | 'evidence_added'; resolution_state: 'unresolved'; workflow_state: ExceptionWorkflow;
  claimant_id: string | null; snoozed_until: string | null; reason: string; actor_id: string | null;
  dependency_id: string | null; created_at: string;
}
export interface ExceptionDependency {
  id: string; case_id: string; assignment_proposal_id: string; extraction_revision_id: string;
  cause: string; created_at: string; revision_number: number; proposal_revision_number: number; proposal_reason: string;
}
export interface ExceptionCommand { action: ExceptionAction; reason: string; snoozed_until: string | null }
export interface RevisionPage<T> { items: T[]; next_before_revision: number | null }
export const exceptionApi = {
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
  append: (id: string, body: ExceptionCommand, expected: string, key: string) => api.post<ExceptionEvent>(
    `/api/exceptions/${id}/events`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected}"` } }),
};
export const exceptionCauses: Record<string, string> = {
  scope_hint_conflict: 'Abrechnungskreis abgleichen', unsupported_scope_type: 'Abrechnungsart noch nicht unterstützt',
};
export const exceptionActions: Record<ExceptionEvent['action'], string> = {
  evidence_added: 'Weiterer Zuordnungsvorschlag erfasst', claim: 'Bearbeitung beansprucht', release: 'Anspruch freigegeben', snooze: 'Zurückgestellt', reopen: 'Wieder geöffnet',
};
