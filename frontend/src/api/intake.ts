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
