import { api } from './client';

export interface AllocationPreview {
  context_decision_id: string; logical_document_id: string; assignment_proposal_id: string; invoice_review_id: string;
  scope_label: string; gross_amount: string; vat_amount: string | null; currency: 'EUR'; can_propose: boolean;
}
export interface AllocationCategory { id: string; plan_version: number; code: string; label: string; is_active: boolean }
export interface AllocationLine { cost_category_id: string; cost_category_plan_version: number; description: string; gross_amount: string; vat_amount: string | null }
export interface AllocationCommand { context_decision_id: string; lines: AllocationLine[]; reason: string; allocation_reviewed: true }
export interface AllocationProposal extends AllocationCommand {
  id: string; document_ref_id: string; logical_document_id: string; scope_id: string; property_id: string; assignment_proposal_id: string; invoice_review_id: string;
  previous_revision_id: string | null; revision_number: number; line_count: number; allocation_kind: 'single_scope_breakdown'; financial_state: 'proposal_only';
  actor_id: string; created_at: string; gross_amount: string; vat_amount: string | null; currency: 'EUR';
  lines: (AllocationLine & { id: string; position: number; category_code: string; category_label: string })[];
}
export interface AllocationHistory { items: AllocationProposal[]; next_before_revision: number | null }
export const allocationProposalsApi = {
  categories: (signal?: AbortSignal) => api.get<AllocationCategory[]>('/api/cost-categories', { signal }),
  preview: (decision: string, signal?: AbortSignal) => api.get<AllocationPreview>(`/api/context-decisions/${decision}/allocation-preview`, { signal }),
  history: (logical: string, signal?: AbortSignal) => api.get<AllocationHistory>(`/api/logical-documents/${logical}/allocation-proposals?limit=20`, { signal }),
  create: (logical: string, body: AllocationCommand, expected: string | null, key: string) => api.post<AllocationProposal>(
    `/api/logical-documents/${logical}/allocation-proposals`, body, { headers: { 'Idempotency-Key': key, 'If-Match': `"${expected ?? 'none'}"` } }),
};

export function exactCents(input: string): bigint | null {
  const value = input.trim().replace(',', '.');
  if (!/^-?(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$/.test(value)) return null;
  const [whole, fraction = ''] = value.replace(/^-/, '').split('.');
  return (value.startsWith('-') ? -1n : 1n) * (BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0')));
}
export function displayCents(value: bigint): string {
  const absolute = value < 0n ? -value : value;
  return `${value < 0n ? '-' : ''}${absolute / 100n},${String(absolute % 100n).padStart(2, '0')}`;
}
