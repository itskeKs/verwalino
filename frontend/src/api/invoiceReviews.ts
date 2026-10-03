import { api } from './client';

export interface InvoiceFields {
  seller_name: string; document_number: string; document_kind: 'invoice' | 'credit_note'; currency: 'EUR';
  invoice_date: string; service_period_from: string; service_period_to: string;
  gross_amount: string; vat_amount: string | null; vat_rate: string | null;
}
export interface InvoiceReview extends InvoiceFields {
  id: string; assignment_proposal_id: string; document_ref_id: string; logical_document_id: string;
  partition_revision_id: string; extraction_revision_id: string; scope_id: string; content_sha256: string;
  previous_revision_id: string | null; revision_number: number; fields_reviewed: true; review_scope: 'invoice_fields';
  reason: string; actor_id: string; created_at: string; assignment_state: 'proposed' | 'blocked'; assignment_blockers: string[];
}
export interface InvoiceReviewCommand extends InvoiceFields { assignment_proposal_id: string; fields_reviewed: true; reason: string }
export interface InvoiceReviewHistory { items: InvoiceReview[]; next_before_revision: number | null; reviewable_assignment_id: string | null }
export const invoiceReviewsApi = {
  history: (logical: string, signal?: AbortSignal) => api.get<InvoiceReviewHistory>(`/api/logical-documents/${logical}/invoice-reviews?limit=20`, { signal }),
  create: (logical: string, body: InvoiceReviewCommand, expected: string | null, key: string) => api.post<InvoiceReview>(
    `/api/logical-documents/${logical}/invoice-reviews`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` } }),
};
