import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AllocationCorrectionPanel } from './AllocationCorrectionPanel';
import type { CorrectionResult } from '../../api/allocationAdoptions';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const amounts = { gross_amount: '119.00', service_period_from: '2026-01-01', service_period_to: '2026-12-31', costs: [{ position: 1, gross_amount: '119.00', vat_amount: null, tenant_amount: null }] };
const impact = { kind: 'economic_plan' as const, id: 'plan', run_type: 'WIRTSCHAFTSPLAN', status: 'finalized' as const, period_start: '2026-01-01', period_end: '2026-12-31', superseded_by_id: 'draft', effects: { exact_cost_consumption: false, potential_calculation_change: false, coverage_renewal: false, source_evidence_review: true }, follow_up: 'review_finalized_successor' as const };
const preview = { can_review: true, preview_digest: 'digest', old: amounts, proposed: amounts, impacts: [impact] };
const props = { allocationId: 'old', reviewId: 'review', parentBusy: false, parentGeneration: 0, receipt: null, onConfirmed: vi.fn() };
const receipt = { receipt: { id: 'receipt', predecessor_id: 'old', successor_id: 'new', reason: 'Reviewed replacement', confirmed_at: '2026-10-03T10:00:00Z', impacts: [{ id: 'impact', snapshot: impact }] }, allocation: { id: 'new', cost_count: 3, source_document_id: 'source-v2' } } as CorrectionResult;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
async function complete() {
  fireEvent.click(screen.getByRole('button', { name: 'Korrekturvorschau laden' }));
  await screen.findByLabelText('Begründung der Korrektur');
  fireEvent.change(screen.getByLabelText('Begründung der Korrektur'), { target: { value: 'Reviewed complete correction' } });
  for (const box of screen.getAllByRole('checkbox')) fireEvent.click(box);
}
it('requires a reason and all three deliberate acknowledgments; edits reset them', async () => {
  const fetch = vi.fn(async () => json(preview)); vi.stubGlobal('fetch', fetch);
  render(<AllocationCorrectionPanel {...props} />); await complete();
  const confirm = screen.getByRole('button', { name: 'Gesamte Aufteilung verbindlich korrigieren' }); expect(confirm).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Begründung der Korrektur'), { target: { value: 'Changed reason' } });
  expect(confirm).toBeDisabled(); expect(screen.getAllByRole('checkbox').every(box => !(box as HTMLInputElement).checked)).toBe(true);
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('retains the exact unknown command and predecessor through head/review refresh, then reloads current receipts', async () => {
  const posts: { url: string; options: RequestInit }[] = []; let fail = true;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push({ url, options }); if (fail) throw new TypeError('Lost response'); return json(receipt, 201); }
    return json(preview);
  }));
  const view = render(<AllocationCorrectionPanel {...props} />); await complete();
  fireEvent.click(screen.getByRole('button', { name: 'Gesamte Aufteilung verbindlich korrigieren' }));
  await screen.findByText(/Keine bestätigte Antwort/); expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  view.rerender(<AllocationCorrectionPanel {...props} allocationId="newer-head" reviewId="new-review" parentGeneration={2} />);
  fail = false; fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Korrektur erneut senden' }));
  await waitFor(() => expect(props.onConfirmed).toHaveBeenCalledOnce());
  expect(posts).toHaveLength(2); expect(posts[1]).toEqual(posts[0]);
  expect(posts[0].url).toBe('/api/allocation-sets/old/corrections'); expect(posts[0].options.headers).toMatchObject({ 'If-Match': '"old"' });
  expect(JSON.parse(posts[0].options.body as string)).toMatchObject({ treatment_review_id: 'review', preview_digest: 'digest', downstream_followups_reviewed: true });
  view.rerender(<AllocationCorrectionPanel {...props} allocationId="new" receipt={receipt} parentGeneration={3} />);
  expect(screen.getByRole('heading', { name: 'Folgearbeiten' })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /Belegversion der Korrektur/ })).toHaveAttribute('href', '/belege/source-v2');
  expect(screen.getByText(/Nachfolger vorhanden; dessen Abschluss muss geprüft/)).toBeInTheDocument();
});
it.each([403, 409])('clears rejected confirmation and requires a fresh preview after %s', async status => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? json({}, status) : json(preview)));
  render(<AllocationCorrectionPanel {...props} />); await complete();
  fireEvent.click(screen.getByRole('button', { name: 'Gesamte Aufteilung verbindlich korrigieren' })); await screen.findByRole('alert');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); expect(screen.queryByText('Dieselbe Korrektur erneut senden')).not.toBeInTheDocument();
  await complete(); expect(screen.getByRole('button', { name: 'Gesamte Aufteilung verbindlich korrigieren' })).toBeEnabled();
});
it('hides retained receipt and cancels the confirmation when parent authority is being refreshed', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(preview)));
  const view = render(<AllocationCorrectionPanel {...props} receipt={receipt} />); await complete();
  view.rerender(<AllocationCorrectionPanel {...props} receipt={receipt} parentBusy />);
  expect(screen.queryByRole('link')).not.toBeInTheDocument(); expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Korrekturvorschau laden' })).toBeDisabled();
});
