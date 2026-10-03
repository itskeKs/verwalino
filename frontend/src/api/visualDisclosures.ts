import { api } from './client';

export type DisclosureScope = { id: string; label: string; scope_type: string };
export type VisualDisclosure = {
  destination: DisclosureScope | null;
  receipt: { id: string; kind: 'published' | 'withdrawn'; scope_id: string | null; created_at: string;
    content_sha256: string; content_byte_size: number; output_page_count: number };
  current: { id: string; kind: 'published' | 'withdrawn'; available: boolean; can_download: boolean; can_withdraw: boolean };
};
export type DisclosureTarget = { current: VisualDisclosure | null; partition_revision_id: string; content_sha256: string; output_page_count: number };
export type DisclosurePublish = { scope_id: string; partition_revision_id: string; content_sha256: string; reason: string; visible_content_reviewed: true };
const headers = (key: string, expected: string | null) => ({ 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` });
export const visualDisclosureApi = {
  target: (artifact: string, scope: string, signal: AbortSignal) => api.get<DisclosureTarget>(`/api/visual-extracts/${artifact}/disclosure-target?scope_id=${scope}`, { signal }),
  publish: (artifact: string, body: DisclosurePublish, expected: string | null, key: string) => api.post<VisualDisclosure>(`/api/visual-extracts/${artifact}/disclosures`, body, { headers: headers(key, expected) }),
  withdraw: (id: string, reason: string, key: string) => api.post<VisualDisclosure>(`/api/visual-disclosures/${id}/withdrawals`, { reason, withdrawal_confirmed: true }, { headers: headers(key, id) }),
  list: (endpoint: string, cursor: string | null, signal: AbortSignal) => api.get<{ items: VisualDisclosure[]; next_cursor: string | null }>(`${endpoint}?limit=10${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`, { signal }),
  content: (id: string) => `/api/visual-disclosures/${id}/content`,
};
