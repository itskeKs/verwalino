import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AllocationAdoptionPanel } from './AllocationAdoptionPanel';
import type { AdoptionPreview, AllocationSet } from '../../api/allocationAdoptions';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const preview: AdoptionPreview = { can_adopt: true, preview_digest: 'digest', blocker: null, scope_id: 'scope', scope_label: 'WEG Test', cost_count: 2, gross_amount: '-119.00' };
const result: AllocationSet = { id: 'set', kind: 'intake_reviewed', source_document_id: 'document', primary_cost_id: 'cost-a', property_id: 'property', scope_id: 'scope', cost_count: 2, financial_state: 'confirmed', reason: 'Reviewed costs and publication', actor_id: 'actor', confirmed_at: '2026-10-03T10:00:00Z', costs: [
  { cost_entry_id: 'cost-a', position: 1, gross_amount: '-59.00', vat_amount: null, tenant_amount: '-10.00' }, { cost_entry_id: 'cost-b', position: 2, gross_amount: '-60.00', vat_amount: null, tenant_amount: '0.00' }] };
const props = { logicalId: 'logical', reviewId: 'review', ready: true, parentBusy: false, parentGeneration: 0 };
async function fill() {
  await screen.findByLabelText('Begründung der Kostenübernahme');
  fireEvent.change(screen.getByLabelText('Begründung der Kostenübernahme'), { target: { value: 'Reviewed costs and publication' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /Alle Teilkosten/ })); fireEvent.click(screen.getByRole('checkbox', { name: /Vollständiges Original/ }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('requires separate financial and disclosure acknowledgments, sends exact review and shows all saved links', async () => {
  const requests: RequestInit[] = []; let committed = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); committed = true; return json(result, 201); }
    return json(url.includes('adoption-preview') ? preview : { item: committed ? result : null });
  }));
  render(<AllocationAdoptionPanel {...props} />); await screen.findByLabelText('Begründung der Kostenübernahme');
  expect(screen.getAllByRole('checkbox').every(item => !(item as HTMLInputElement).checked)).toBe(true);
  await fill(); fireEvent.change(screen.getByLabelText('Begründung der Kostenübernahme'), { target: { value: 'Changed reason' } });
  expect(screen.getAllByRole('checkbox').every(item => !(item as HTMLInputElement).checked)).toBe(true);
  fireEvent.click(screen.getByRole('checkbox', { name: /Alle Teilkosten/ }));
  expect(screen.getByRole('button', { name: 'Teilkosten bestätigen und Original freigeben' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: /Vollständiges Original/ })); fireEvent.click(screen.getByRole('button', { name: 'Teilkosten bestätigen und Original freigeben' }));
  await screen.findByText('Kostenübernahme bestätigt');
  expect(JSON.parse(String(requests[0].body))).toEqual({ treatment_review_id: 'review', preview_digest: 'digest', reason: 'Changed reason', costs_confirmed: true, whole_original_disclosure_reviewed: true });
  expect(new Headers(requests[0].headers).get('If-Match')).toBe('"review"');
  expect(screen.getByRole('link', { name: /Teilkosten 2/ })).toHaveAttribute('href', '/kosten/cost-b');
  expect(screen.getByRole('link', { name: /Freigegebenes Original/ })).toHaveAttribute('href', '/belege/document');
});
it('keeps an unknown exact command after committed history and denied newer treatment', async () => {
  const requests: RequestInit[] = []; let committed = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); committed = true; return json({}, 503); }
    if (url.includes('/new/')) return json({}, 404);
    return json(url.includes('adoption-preview') ? { ...preview, can_adopt: !committed } : { item: committed ? result : null });
  }));
  const rendered = render(<AllocationAdoptionPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Teilkosten bestätigen und Original freigeben' }));
  await screen.findByText(/Keine bestätigte Antwort/);
  rendered.rerender(<AllocationAdoptionPanel {...props} reviewId="new" parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Kostenübernahme erneut senden' })).toBeEnabled());
  expect(screen.getByLabelText('Begründung der Kostenübernahme')).toBeDisabled(); expect(screen.getByText('Kostenübernahme bestätigt')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Kostenübernahme erneut senden' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  for (const name of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[0].headers).get(name)).toBe(new Headers(requests[1].headers).get(name));
});
it('requires a fresh confirmation after a changed duplicate snapshot', async () => {
  let current = preview;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('adoption-preview') ? current : { item: null })));
  const rendered = render(<AllocationAdoptionPanel {...props} />); await fill(); current = { ...preview, preview_digest: 'changed' };
  rendered.rerender(<AllocationAdoptionPanel {...props} parentGeneration={1} />);
  const refresh = await screen.findByRole('button', { name: 'Aktuelle Übernahmegrundlage prüfen' });
  expect(screen.getByRole('button', { name: 'Teilkosten bestätigen und Original freigeben' })).toBeDisabled();
  fireEvent.click(refresh); expect(screen.getByLabelText('Begründung der Kostenübernahme')).toHaveValue('');
  expect(screen.getAllByRole('checkbox').every(item => !(item as HTMLInputElement).checked)).toBe(true);
});
it('clears a denied retained source and never restores its pending retry', async () => {
  let deny = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 503) : deny ? json({}, 404) : json(url.includes('adoption-preview') ? preview : { item: null })));
  const rendered = render(<AllocationAdoptionPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Teilkosten bestätigen und Original freigeben' })); await screen.findByText(/Keine bestätigte Antwort/);
  deny = true; rendered.rerender(<AllocationAdoptionPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.queryByLabelText('Begründung der Kostenübernahme')).not.toBeInTheDocument());
  deny = false; rendered.rerender(<AllocationAdoptionPanel {...props} parentGeneration={2} />);
  await screen.findByLabelText('Begründung der Kostenübernahme'); expect(screen.getByLabelText('Begründung der Kostenübernahme')).toHaveValue('');
  expect(screen.queryByRole('button', { name: 'Dieselbe Kostenübernahme erneut senden' })).not.toBeInTheDocument();
});
it('blocks new commands when the server reports an unresolved source conflict', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('adoption-preview') ? { ...preview, can_adopt: false } : { item: null })));
  render(<AllocationAdoptionPanel {...props} />); await screen.findByText(/Übernahme derzeit nicht möglich/);
  expect(screen.queryByLabelText('Begründung der Kostenübernahme')).not.toBeInTheDocument();
});

it('preserves an unknown correction across parent GET failure and recovers the exact request', async () => {
  let failedRead = false; let accepted = false; const posts: { url: string; init: RequestInit }[] = [];
  const amounts = { gross_amount: '-119.00', service_period_from: '2026-01-01', service_period_to: '2026-12-31', costs: result.costs };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    if (init.method === 'POST') { posts.push({ url, init }); accepted = true; return json({}, 503); }
    if (url.includes('correction-preview')) return json({ can_review: true, preview_digest: 'correction-digest', old: amounts, proposed: amounts, impacts: [] });
    if (url.includes('allocation-adoptions')) return failedRead ? json({}, 503) : json({ item: { ...result, id: accepted ? 'new-head' : result.id } });
    if (url.includes('adoption-preview')) return json({ ...preview, can_adopt: false });
    return json({ options: [], items: [] });
  }));
  const view = render(<AllocationAdoptionPanel {...props} />);
  await screen.findByText('Kostenübernahme bestätigt');
  fireEvent.click(screen.getByRole('button', { name: 'Korrekturvorschau laden' }));
  await screen.findByLabelText('Begründung der Korrektur');
  fireEvent.change(screen.getByLabelText('Begründung der Korrektur'), { target: { value: 'Reviewed correction' } });
  for (const box of screen.getAllByRole('checkbox')) fireEvent.click(box);
  fireEvent.click(screen.getByRole('button', { name: 'Gesamte Aufteilung verbindlich korrigieren' }));
  await screen.findByRole('button', { name: 'Dieselbe Korrektur erneut senden' });
  failedRead = true; view.rerender(<AllocationAdoptionPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.queryByText('Kostenübernahme bestätigt')).not.toBeInTheDocument());
  expect(screen.queryByRole('button', { name: 'Dieselbe Korrektur erneut senden' })).not.toBeInTheDocument();
  failedRead = false; view.rerender(<AllocationAdoptionPanel {...props} parentGeneration={2} />);
  const retry = await screen.findByRole('button', { name: 'Dieselbe Korrektur erneut senden' });
  await waitFor(() => expect(retry).toBeEnabled()); fireEvent.click(retry);
  await waitFor(() => expect(posts).toHaveLength(2)); expect(posts[1]).toEqual(posts[0]); expect(posts[1].url).toBe('/api/allocation-sets/set/corrections');
});
