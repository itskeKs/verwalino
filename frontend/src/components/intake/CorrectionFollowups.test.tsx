import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { CorrectionFollowups } from './CorrectionFollowups';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const item = { id: 'impact', snapshot: { kind: 'settlement_run' as const, id: 'report', run_type: 'VERMOEGENSBERICHT', status: 'draft' as const,
  period_start: '2026-01-01', period_end: '2026-12-31', superseded_by_id: null, effects: { exact_cost_consumption: false, potential_calculation_change: false, coverage_renewal: true, source_evidence_review: true }, follow_up: 'refresh_and_revalidate' as const } };
const status = { impact_id: 'impact', status: 'open', ready: true, blocker: null, preview_digest: 'basis', kind: 'settlement_run', target_id: 'successor', source_document_id: 'source', source_review_required: true, history: [] };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function prepare() {
  fireEvent.click(screen.getByRole('button', { name: 'Bearbeitungsstand prüfen' }));
  await screen.findByLabelText('Begründung des Abschlusses');
  fireEvent.change(screen.getByLabelText('Begründung des Abschlusses'), { target: { value: 'Reviewed actual processing' } });
}
it('requires source review and deliberate confirmation, and links the computed successor', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(status)));
  render(<CorrectionFollowups items={[item]} parentBusy={false} generation={0} />); await prepare();
  const save = screen.getByRole('button', { name: 'Folgearbeit verbindlich abschließen' }); expect(save).toBeDisabled();
  const boxes = screen.getAllByRole('checkbox'); fireEvent.click(boxes[0]); expect(save).toBeDisabled(); fireEvent.click(boxes[1]); expect(save).toBeEnabled();
  expect(screen.getByRole('link', { name: 'Geprüften Finanzvorgang öffnen' })).toHaveAttribute('href', '/vermoegensberichte/successor');
  fireEvent.change(screen.getByLabelText('Begründung des Abschlusses'), { target: { value: 'Changed review' } }); expect(save).toBeDisabled();
});
it('retains the exact unknown request across a missing parent receipt and a newer head', async () => {
  const posts: { url: string; options: RequestInit }[] = []; let lost = true;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push({ url, options }); if (lost) throw new TypeError('Lost reply'); return json({ id: 'receipt' }, 201); }
    return json(status);
  }));
  const view = render(<CorrectionFollowups items={[item]} parentBusy={false} generation={0} />); await prepare();
  for (const box of screen.getAllByRole('checkbox')) fireEvent.click(box);
  fireEvent.click(screen.getByRole('button', { name: 'Folgearbeit verbindlich abschließen' })); await screen.findByText(/Keine bestätigte Antwort/);
  view.rerender(<CorrectionFollowups items={[]} parentBusy generation={1} />); expect(screen.queryByRole('button')).not.toBeInTheDocument();
  view.rerender(<CorrectionFollowups items={[]} parentBusy={false} generation={2} />);
  lost = false; fireEvent.click(screen.getByRole('button', { name: 'Denselben Abschluss erneut senden' }));
  await waitFor(() => expect(posts).toHaveLength(2)); expect(posts[1]).toEqual(posts[0]);
  expect(posts[0].url).toBe('/api/allocation-correction-impacts/impact/completions');
});
it.each([403, 409])('clears a rejected request after %s and requires a fresh review', async code => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, options: RequestInit) => options.method === 'POST' ? json({}, code) : json(status)));
  render(<CorrectionFollowups items={[item]} parentBusy={false} generation={0} />); await prepare();
  for (const box of screen.getAllByRole('checkbox')) fireEvent.click(box);
  fireEvent.click(screen.getByRole('button', { name: 'Folgearbeit verbindlich abschließen' })); await screen.findByRole('alert');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); expect(screen.queryByText('Denselben Abschluss erneut senden')).not.toBeInTheDocument();
  await prepare(); expect(screen.getByRole('button', { name: 'Folgearbeit verbindlich abschließen' })).toBeDisabled();
});
it('shows retained history separately from changed current validity', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...status, status: 'changed', ready: false, blocker: 'Financial computation is no longer current', history: [{ id: 'old', completed_at: '2026-10-03T10:00:00Z', reason: 'Earlier checked state' }] })));
  render(<CorrectionFollowups items={[item]} parentBusy={false} generation={0} />);
  fireEvent.click(screen.getByRole('button', { name: 'Bearbeitungsstand prüfen' })); await screen.findByText(/Nach dem letzten Abschluss/);
  expect(screen.getByText(/Earlier checked state/)).toBeInTheDocument(); expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});
