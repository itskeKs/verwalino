import { api } from './client';
import type { ContextEvidence } from './contextCandidates';
export interface ContextMapping {
  id: string; property_id: string; scope_id: string; identifier_kind: 'property_id' | 'meter_number'; identifier_value: string;
  revision_number: number; state: 'active' | 'revoked'; valid_from: string; valid_to: string; policy: 'manual-context-reuse-v1';
}
export interface MappingOption extends Omit<ContextEvidence, 'kind'> { kind: ContextMapping['identifier_kind']; current: ContextMapping | null }
export interface MappingOptions { allocation_set_id: string; can_activate: boolean; options: MappingOption[]; property_id?: string; scope_id?: string; scope_label?: string }
export interface MappingCommand {
  allocation_set_id: string; identifier_kind: ContextMapping['identifier_kind']; identifier_value: string; page_number: number;
  valid_from: string; valid_to: string; reason: string; reuse_consented: true;
}
export interface MappingStatus { receipt: ContextMapping; current: ContextMapping; master_current: boolean; can_prepare: boolean }
export const contextMappingsApi = {
  options: (allocation: string, retained: Pick<MappingOption, 'kind' | 'value'> | null, signal?: AbortSignal) => api.get<MappingOptions>(
    `/api/allocation-sets/${allocation}/mapping-options${retained ? `?identifier_kind=${retained.kind}&identifier_value=${encodeURIComponent(retained.value)}` : ''}`, { signal }),
  activate: (body: MappingCommand, expected: string | null, key: string) => api.post<ContextMapping>('/api/context-mappings', body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` } }),
  status: (version: string, signal?: AbortSignal) => api.get<MappingStatus>(`/api/context-mappings/${version}`, { signal }),
  revoke: (version: string, reason: string, key: string) => api.post<ContextMapping>(`/api/context-mappings/${version}/revocations`, { reason }, { headers: { 'Idempotency-Key': key, 'If-Match': `"${version}"` } }),
};
