import { api } from './client';

export type VisualExtract = {
  id: string; logical_document_id: string; document_ref_id: string; partition_revision_id: string;
  page_start: number; page_end: number; output_page_count: number;
  source_sha256: string; content_sha256: string; content_byte_size: number;
  reason: string; created_at: string;
};
export type ExtractCommand = { partition_revision_id: string; reason: string };
export const visualExtractApi = {
  create: (logical: string, body: ExtractCommand, key: string) => api.post<VisualExtract>(
    `/api/logical-documents/${logical}/visual-extracts`, body, { headers: { 'Idempotency-Key': key } }),
  history: (logical: string, cursor: string | null, signal: AbortSignal) => api.get<{ items: VisualExtract[]; next_cursor: string | null }>(
    `/api/logical-documents/${logical}/visual-extracts?limit=10${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal }),
  content: (id: string) => `/api/visual-extracts/${id}/content`,
};
