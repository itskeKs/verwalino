import { api, uploadFormData } from './client';

export interface IntakeManifestItem {
  client_item_key: string; filename: string; mime_type: string;
  content_sha256: string; content_byte_size: number;
}
export interface IntakeHeader { id: string; created_at: string; scope_hint_id: string | null; item_count: number }
export type IntakeStatus = 'waiting' | 'received' | 'processing' | 'review' | 'confirmed' | 'failed';
export interface IntakeItem extends IntakeManifestItem {
  id: string; position: number; status: IntakeStatus; original_id: string | null; job_id: string | null;
  fence: string | null; error_code: string | null; can_upload: boolean; can_retry: boolean; can_export: boolean;
  stage: 'inspection' | 'preparation' | 'extraction' | 'review'; document_ref_id: string | null;
  logical_counts: { total: number; processing: number; review: number; failed: number };
}
export interface IntakeBatch extends IntakeHeader {
  items: IntakeItem[]; counts: Record<IntakeStatus, number>; revision: string; next_cursor: string | null;
}
export interface IntakeCreate { scope_hint_id: string | null; items: IntakeManifestItem[] }
export const intakeApi = {
  list: (cursor?: string, signal?: AbortSignal) => api.get<{ items: IntakeHeader[]; next_cursor: string | null }>(
    '/api/intake-batches' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''), { signal }),
  create: (body: IntakeCreate, key: string) => api.post<IntakeHeader>('/api/intake-batches', body, { headers: { 'Idempotency-Key': key } }),
  get: (id: string, limit = 50, signal?: AbortSignal) => api.get<IntakeBatch>(`/api/intake-batches/${id}?limit=${limit}`, { signal }),
  upload: (id: string, key: string, file: File, mime: string) => {
    const form = new FormData(); form.append('client_item_key', key);
    form.append('file', new Blob([file], { type: mime }), file.name);
    return uploadFormData<{ original_id: string; job_id: string; status: 'received' }>(`/api/intake-batches/${id}/items`, form);
  },
  retry: (body: { job_id: string; expected_fence: string; reason: string }, key: string) =>
    api.post('/api/intake-batches/retry', body, { headers: { 'Idempotency-Key': key } }),
  content: (id: string) => `/api/intake-batches/originals/${id}/content`,
};

export interface LogicalDocument {
  id: string; document_ref_id: string; page_start: number | null; page_end: number | null;
  verified_page_count: number | null;
  origin: 'manual' | 'inspection'; review_state: 'unreviewed'; reason: string;
}
export interface ExtractionStatus {
  id: string; status: string; attempt_number: number; fence: string | null; error_code: string | null; can_retry: boolean;
}
export interface FieldEvidence {
  field_name: string; value: string; confidence: number | string | null; locator_status: 'unlocated' | 'page_quote' | 'xml_path';
  page_number: number | null; quotation: string | null; xml_path: string | null; method: string;
}
export interface ExtractionRevision {
  id: string; document_ref_id: string; logical_document_id: string; created_at: string; status: string; error_code: string | null;
  processor_version: string; payload: Record<string, unknown> | null; fields: FieldEvidence[];
}
export const extractionApi = {
  children: (ref: string, signal?: AbortSignal) => api.get<{ items: LogicalDocument[] }>(`/api/logical-documents?document_ref_id=${encodeURIComponent(ref)}`, { signal }),
  status: (id: string, signal?: AbortSignal) => api.get<ExtractionStatus>(`/api/logical-documents/${id}/processing`, { signal }),
  history: (ref: string, child: string, signal?: AbortSignal) => api.get<{ items: Pick<ExtractionRevision, 'id'>[] }>(
    `/api/document-refs/${ref}/extractions?logical_document_id=${encodeURIComponent(child)}`, { signal }),
  revision: (id: string, signal?: AbortSignal) => api.get<ExtractionRevision>(`/api/extraction-revisions/${id}`, { signal }),
  retry: (id: string, body: { expected_fence: string; reason: string }, key: string) => api.post(`/api/logical-documents/${id}/retry-extraction`, body, { headers: { 'Idempotency-Key': key } }),
};

export function intakeMime(file: File): string {
  if (file.type === 'text/xml') return 'application/xml';
  if (['application/pdf', 'application/xml', 'image/png', 'image/jpeg'].includes(file.type)) return file.type;
  const extension = file.name.split('.').pop()?.toLowerCase();
  return ({ pdf: 'application/pdf', xml: 'application/xml', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg' } as Record<string, string>)[extension ?? ''] ?? '';
}
export async function fileDigest(file: File): Promise<string> {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer())))
    .map(value => value.toString(16).padStart(2, '0')).join('');
}

export interface SourcePartition {
  id: string; document_ref_id: string; previous_revision_id: string | null; revision_number: number;
  content_sha256: string; verified_page_count: number | null; visual_pages_reviewed: true;
  reason: string; created_at: string; logical_document_ids: string[];
}
export interface PartitionReview { logical_document_ids: string[]; reason: string; visual_pages_reviewed: true }
export interface PartitionState { current: SourcePartition | null; can_review: boolean; can_export: boolean }
export const partitionApi = {
  get: (ref: string, signal?: AbortSignal) => api.get<PartitionState>(`/api/document-refs/${ref}/partition`, { signal }),
  original: (ref: string, signal?: AbortSignal) => api.get<{ source_id: string; source_kind: string }>(`/api/document-refs/${ref}`, { signal }),
  review: (ref: string, body: PartitionReview, expected: string | null, key: string) => api.post<SourcePartition>(
    `/api/document-refs/${ref}/partition`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` } }),
  createRange: (body: { document_ref_id: string; page_start: number; page_end: number; reason: string }, key: string) =>
    api.post<LogicalDocument>('/api/logical-documents', body, { headers: { 'Idempotency-Key': key } }),
};


export interface AssignmentProposal {
  id: string; document_ref_id: string; logical_document_id: string; partition_revision_id: string; extraction_revision_id: string;
  scope_id: string; scope_label: string; scope_type: string; scope_hint_id: string | null; previous_proposal_id: string | null;
  revision_number: number; method: 'manual_selection'; state: 'proposed' | 'blocked'; blockers: string[]; reason: string; created_at: string;
}
export interface AssignmentCreate { partition_revision_id: string; extraction_revision_id: string; scope_id: string; reason: string }
export const assignmentApi = {
  history: (logical: string, signal?: AbortSignal) => api.get<{ items: AssignmentProposal[]; next_before_revision: number | null }>(
    `/api/logical-documents/${logical}/assignment-proposals?limit=20`, { signal }),
  create: (logical: string, body: AssignmentCreate, expected: string | null, key: string) => api.post<AssignmentProposal>(
    `/api/logical-documents/${logical}/assignment-proposals`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` } }),
};
