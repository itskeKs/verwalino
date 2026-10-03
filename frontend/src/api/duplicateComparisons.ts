import { api } from './client';
import type { InvoiceReview } from './invoiceReviews';

export type DuplicateVerdict = 'same_document' | 'distinct_documents' | 'replacement_requested';
export interface DuplicateEvidence {
  normalization_version: string; match_reasons: ('same_bytes_and_range' | 'same_reviewed_identity')[]; kind_conflict: boolean;
  subject: { content_sha256: string; page_start: number | null; page_end: number | null };
  reference: { content_sha256: string; page_start: number | null; page_end: number | null };
  field_disagreements: string[];
}
export interface DuplicateComparison {
  id: string; scope_id: string; subject_review_id: string; reference_review_id: string; subject_logical_id: string; reference_logical_id: string;
  previous_revision_id: string | null; revision_number: number; verdict: DuplicateVerdict; financial_state: 'evidence_only'; evidence: DuplicateEvidence;
  reason: string; actor_id: string; created_at: string;
}
export interface DuplicateCandidate {
  review_id: string; logical_document_id: string; document_ref_id: string; seller_name: string; document_number: string;
  document_kind: 'invoice' | 'credit_note'; gross_amount: string; currency: string; can_compare: boolean;
  evidence: DuplicateEvidence; latest_comparison: DuplicateComparison | null;
}
export interface DuplicatePage { scope_id: string; items: DuplicateCandidate[]; next_cursor: string | null; financial_state: 'evidence_only' }
export interface DuplicateCommand { reference_review_id: string; verdict: DuplicateVerdict; reason: string; comparison_reviewed: true }
export interface DuplicateHistory { items: DuplicateComparison[]; next_before_revision: number | null }
export const duplicateComparisonsApi = {
  candidates: (review: string, cursor: string | null, signal?: AbortSignal) => api.get<DuplicatePage>(`/api/invoice-reviews/${review}/duplicate-candidates?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal }),
  review: (review: string, signal?: AbortSignal) => api.get<InvoiceReview>(`/api/invoice-reviews/${review}`, { signal }),
  history: (subject: string, reference: string, signal?: AbortSignal) => api.get<DuplicateHistory>(`/api/invoice-reviews/${subject}/duplicate-comparisons?reference_review_id=${reference}&limit=20`, { signal }),
  create: (subject: string, body: DuplicateCommand, expected: string | null, key: string) => api.post<DuplicateComparison>(`/api/invoice-reviews/${subject}/duplicate-comparisons`, body,
    { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` } }),
};
