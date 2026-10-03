import { afterEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AllocationReplacementPanel } from './AllocationReplacementPanel';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const amounts = { gross_amount: '119.00', service_period_from: '2026-01-01', service_period_to: '2026-12-31', costs: [{ position: 1, gross_amount: '119.00', vat_amount: null, tenant_amount: null }] };
const preview = { can_review: true, preview_digest: 'digest', old: amounts, proposed: { ...amounts, gross_amount: '149.00' }, impacts: [] };
const candidate = { id: 'old', source_document_id: 'old-source', document_number: 'R-42', seller_name: 'Utility', gross_amount: '119.00', confirmed_at: '2026-10-03T10:00:00Z' };
const props = { reviewId: 'review', enabled: true, parentBusy: false, generation: 0, onPending: vi.fn(), onConfirmed: vi.fn() };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
async function select() {
  fireEvent.click(screen.getByRole('button', { name: 'Bisherigen Beleg suchen' }));
  await screen.findByRole('combobox'); fireEvent.change(screen.getByRole('combobox'), { target: { value: 'old' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ersetzungsvorschau laden' }));
  await screen.findByLabelText('Begründung der Belegersetzung');
}
async function confirm() {
  await select(); fireEvent.change(screen.getByLabelText('Begründung der Belegersetzung'), { target: { value: 'Revised original explicitly replaces retained source' } });
  for (const box of screen.getAllByRole('checkbox')) fireEvent.click(box);
  fireEvent.click(screen.getByRole('button', { name: 'Beleg und alle Teilkosten verbindlich ersetzen' }));
}
it('compares old and new totals and requires explicit source selection and every acknowledgment', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('replacement-candidates') ? { items: [candidate], next_after: null } : preview)));
  render(<AllocationReplacementPanel {...props} />); await select();
  expect(screen.getByRole('link', { name: 'Bisheriges aufbewahrtes Original öffnen' })).toHaveAttribute('href', '/belege/old-source');
  expect(screen.getByText('149.00 EUR · 1 Teilkosten')).toBeInTheDocument();
  const button = screen.getByRole('button', { name: 'Beleg und alle Teilkosten verbindlich ersetzen' }); expect(button).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Begründung der Belegersetzung'), { target: { value: 'Reviewed source change' } });
  for (const box of screen.getAllByRole('checkbox')) fireEvent.click(box);
  expect(button).toBeEnabled(); fireEvent.change(screen.getByLabelText('Begründung der Belegersetzung'), { target: { value: 'Changed source reason' } });
  expect(button).toBeDisabled();
});
it('freezes predecessor, source review, route, body and key while a lost result is retried after refresh', async () => {
  const posts: { url: string; options: RequestInit }[] = []; let lost = true;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') { posts.push({ url, options }); return json({}, lost ? 503 : 201); }
    return json(url.includes('replacement-candidates') ? { items: [candidate], next_after: null } : preview);
  }));
  const view = render(<AllocationReplacementPanel {...props} />); await confirm(); await screen.findByText(/Keine bestätigte Antwort/);
  expect(props.onPending).toHaveBeenCalledWith(true);
  view.rerender(<AllocationReplacementPanel {...props} reviewId={null} enabled={false} generation={1} />);
  const retry = await screen.findByRole('button', { name: 'Dieselbe Belegersetzung erneut senden' }); await waitFor(() => expect(retry).toBeEnabled());
  lost = false; fireEvent.click(retry); await waitFor(() => expect(props.onConfirmed).toHaveBeenCalledOnce());
  expect(posts).toHaveLength(2); expect(posts[1]).toEqual(posts[0]);
  expect(posts[0].url).toBe('/api/allocation-sets/old/source-replacements');
  expect(posts[0].options.headers).toMatchObject({ 'If-Match': '"old"' });
  expect(JSON.parse(String(posts[0].options.body))).toMatchObject({ treatment_review_id: 'review', preview_digest: 'digest', whole_original_disclosure_reviewed: true });
});
it('clears a pending replacement after access denial and cannot resurrect it from a late POST failure', async () => {
  let denied = false; let finish!: (value: Response) => void;
  vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit) => {
    if (options.method === 'POST') return new Promise<Response>(resolve => { finish = resolve; });
    return denied ? json({}, 404) : json(url.includes('replacement-candidates') ? { items: [candidate], next_after: null } : preview);
  }));
  const view = render(<AllocationReplacementPanel {...props} />); await confirm();
  denied = true; view.rerender(<AllocationReplacementPanel {...props} generation={1} />);
  await screen.findByText('Berechtigung für diese Ersetzung nicht mehr vorhanden.');
  await act(async () => finish(json({}, 503)));
  expect(screen.queryByRole('button', { name: 'Dieselbe Belegersetzung erneut senden' })).not.toBeInTheDocument();
  expect(props.onPending).toHaveBeenLastCalledWith(false); expect(props.onConfirmed).not.toHaveBeenCalled();
});
it('drops stale selection and preview when review context changes', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('replacement-candidates') ? { items: [candidate], next_after: null } : preview)));
  const view = render(<AllocationReplacementPanel {...props} />); await select();
  view.rerender(<AllocationReplacementPanel {...props} reviewId="new-review" generation={1} />);
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument(); expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
});
it.each([404, 503])('clears cached source details only for authorization denial, status %s', async status => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('replacement-candidates') ? json({ items: [candidate], next_after: null }) : json({}, status)));
  render(<AllocationReplacementPanel {...props} />);
  fireEvent.click(screen.getByRole('button', { name: 'Bisherigen Beleg suchen' }));
  await screen.findByRole('combobox'); fireEvent.change(screen.getByRole('combobox'), { target: { value: 'old' } });
  fireEvent.click(screen.getByRole('button', { name: 'Ersetzungsvorschau laden' }));
  await screen.findByRole('alert');
  if (status === 404) {
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument(); expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByText(/Utility/)).not.toBeInTheDocument();
  } else expect(screen.getByRole('link', { name: 'Bisheriges aufbewahrtes Original öffnen' })).toBeInTheDocument();
});
