import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AllocationCorrectionPreview } from './AllocationCorrectionPreview';
import type { CorrectionPreview } from '../../api/allocationAdoptions';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const amounts = { gross_amount: '-119.00', service_period_from: '2025-01-01', service_period_to: '2025-12-31', costs: [{ position: 1, gross_amount: '-119.00', vat_amount: null, tenant_amount: '-10.00' }] };
const value: CorrectionPreview = { can_review: true, preview_digest: 'digest', blocker: null, financial_state: 'preview_only', old: amounts, proposed: amounts,
  impacts: [{ kind: 'economic_plan', id: 'plan', run_type: 'WIRTSCHAFTSPLAN', status: 'draft', period_start: '2025-01-01', period_end: '2025-12-31', superseded_by_id: null,
    effects: { exact_cost_consumption: false, potential_calculation_change: false, coverage_renewal: true, source_evidence_review: false }, follow_up: 'refresh_and_revalidate' },
  { kind: 'settlement_run', id: 'settlement', run_type: 'WEG_JAHRESABRECHNUNG', status: 'finalized', period_start: '2025-01-01', period_end: '2025-12-31', superseded_by_id: 'new',
    effects: { exact_cost_consumption: true, potential_calculation_change: true, coverage_renewal: false, source_evidence_review: true }, follow_up: 'review_finalized_successor' }] };
const props = { allocationId: 'set', reviewId: 'review', parentBusy: false, parentGeneration: 0 };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('loads explicitly, explains different effects and never presents a financial confirmation', async () => {
  const fetch = vi.fn(async () => json(value)); vi.stubGlobal('fetch', fetch);
  render(<AllocationCorrectionPreview {...props} />); expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Korrekturvorschau laden' }));
  await screen.findByText(/Nur Vorschau/); expect(fetch.mock.calls).toHaveLength(1);
  expect(screen.getByText(/Dies allein belegt keinen falschen Betrag/)).toBeInTheDocument();
  expect(screen.getByText(/Nachfolger vorhanden; dessen Abschluss/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /Wirtschaftsplan/ })).toHaveAttribute('href', '/wirtschaftsplaene/plan');
  expect(screen.getByRole('link', { name: /WEG-Jahresabrechnung/ })).toHaveAttribute('href', '/weg-abrechnungen/settlement');
  expect(screen.getAllByRole('button')).toHaveLength(1);
});
it('clears prior details immediately when rechecking and keeps no partial result after denial', async () => {
  let fail = false; vi.stubGlobal('fetch', vi.fn(async () => json(fail ? {} : value, fail ? 404 : 200)));
  render(<AllocationCorrectionPreview {...props} />); fireEvent.click(screen.getByRole('button')); await screen.findByText(/Nur Vorschau/);
  fail = true; fireEvent.click(screen.getByRole('button')); expect(screen.queryByRole('link')).not.toBeInTheDocument();
  await screen.findByRole('alert'); expect(screen.queryByText(/Nur Vorschau/)).not.toBeInTheDocument();
});
it('discards a late response after review or parent authorization changes', async () => {
  let resolve!: (value: Response) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(done => { resolve = done; })));
  const rendered = render(<AllocationCorrectionPreview {...props} />); fireEvent.click(screen.getByRole('button'));
  rendered.rerender(<AllocationCorrectionPreview {...props} reviewId="new" parentBusy />);
  resolve(json(value)); await waitFor(() => expect(screen.getByRole('button')).toBeDisabled());
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
  rendered.rerender(<AllocationCorrectionPreview {...props} reviewId="new" parentGeneration={1} />);
  expect(screen.queryByText(/Nur Vorschau/)).not.toBeInTheDocument();
});
it('shows a generic complete-preview blocker without displaying injected partial dependencies', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...value, can_review: false, preview_digest: null })));
  render(<AllocationCorrectionPreview {...props} />); fireEvent.click(screen.getByRole('button'));
  await screen.findByText(/Vollständige Korrekturvorschau derzeit nicht möglich/);
  expect(screen.queryByRole('link')).not.toBeInTheDocument(); expect(screen.queryByText('Bisher bestätigt')).not.toBeInTheDocument();
});
