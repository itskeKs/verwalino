import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { exactCents, displayCents, type AllocationPreview } from '../../api/allocationProposals';
import { AllocationProposalPanel } from './AllocationProposalPanel';

vi.mock('./AllocationTreatmentPanel', () => ({ AllocationTreatmentPanel: () => null }));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const preview: AllocationPreview = { context_decision_id: 'context', logical_document_id: 'logical', assignment_proposal_id: 'proposal', invoice_review_id: 'review', scope_label: 'Synthetic scope', gross_amount: '119.00', vat_amount: null, currency: 'EUR', can_propose: true };
const categories = [{ id: 'category', plan_version: 1, code: 'TEST', label: 'Test category', is_active: true }];
const empty = { items: [], next_before_revision: null };
const props = { logicalId: 'logical', contextDecisionId: 'context', ready: true, parentBusy: false, parentGeneration: 0, onDenied: vi.fn() };
function response(url: string, value = preview) { return json(url.includes('allocation-preview') ? value : url.includes('cost-categories') ? categories : empty); }
async function fill() {
  await screen.findByLabelText('Kostenart · Zeile 1');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Aufteilungen aktualisieren' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Kostenart · Zeile 1'), { target: { value: 'category' } });
  fireEvent.change(screen.getByLabelText('Beschreibung · Zeile 1'), { target: { value: 'Human reviewed service' } });
  fireEvent.change(screen.getByLabelText('Begründung der Aufteilung in Kostenarten'), { target: { value: 'Compared source line totals' } });
  fireEvent.click(screen.getByRole('checkbox'));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); props.onDenied.mockClear(); });

it('conserves cents across decimal boundaries without binary float rounding', () => {
  expect(exactCents('0.10')! + exactCents('0,20')!).toBe(exactCents('0.30'));
  expect(exactCents('-999999999999.99')).toBe(-99999999999999n);
  expect(displayCents(-11901n)).toBe('-119,01');
  for (const value of ['1.001', 'NaN', '1e2', '1.000,00', '1000000000000.00', '']) expect(exactCents(value)).toBeNull();
});

it('requires exact balanced amounts and retains unknown VAT without category defaults', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return response(url); }));
  render(<AllocationProposalPanel {...props} />); await fill();
  expect(screen.getByLabelText('Umsatzsteuer · Zeile 1')).toBeDisabled(); expect(screen.getByLabelText('Umsatzsteuer · Zeile 1')).toHaveValue('');
  fireEvent.change(screen.getByLabelText('Brutto · Zeile 1'), { target: { value: '118,99' } }); expect(screen.getByRole('checkbox')).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Aufteilungsvorschlag speichern' }));
  await screen.findByText(/exakte Brutto-/); expect(requests).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('Brutto · Zeile 1'), { target: { value: '119,00' } }); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilungsvorschlag speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  expect(JSON.parse(String(requests[0].body))).toEqual({ context_decision_id: 'context', lines: [{ cost_category_id: 'category', cost_category_plan_version: 1, description: 'Human reviewed service', gross_amount: '119.00', vat_amount: null }], reason: 'Compared source line totals', allocation_reviewed: true });
});

it('checks negative split and known VAT totals separately', async () => {
  const requests: RequestInit[] = []; const credit = { ...preview, gross_amount: '-119.00', vat_amount: '-19.00' };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return response(url, credit); }));
  render(<AllocationProposalPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zeile hinzufügen' }));
  fireEvent.change(screen.getByLabelText('Brutto · Zeile 1'), { target: { value: '-59,00' } }); fireEvent.change(screen.getByLabelText('Umsatzsteuer · Zeile 1'), { target: { value: '-9,00' } });
  fireEvent.change(screen.getByLabelText('Kostenart · Zeile 2'), { target: { value: 'category' } }); fireEvent.change(screen.getByLabelText('Beschreibung · Zeile 2'), { target: { value: 'Second service' } });
  fireEvent.change(screen.getByLabelText('Brutto · Zeile 2'), { target: { value: '-60,00' } }); fireEvent.change(screen.getByLabelText('Umsatzsteuer · Zeile 2'), { target: { value: '-9,99' } });
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Aufteilungsvorschlag speichern' })); await screen.findByText(/exakte Brutto-/); expect(requests).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('Umsatzsteuer · Zeile 2'), { target: { value: '-10,00' } }); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilungsvorschlag speichern' })); await waitFor(() => expect(requests).toHaveLength(1));
  expect(JSON.parse(String(requests[0].body)).lines.map((line: { gross_amount: string }) => line.gross_amount)).toEqual(['-59.00', '-60.00']);
});

it('privately preserves unknown commands across missing parent context and replays unchanged', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return response(url); }));
  const rendered = render(<AllocationProposalPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Aufteilungsvorschlag speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  rendered.rerender(<AllocationProposalPanel {...props} contextDecisionId={null} ready={false} />);
  await waitFor(() => expect(screen.queryByText(/Synthetic scope/)).not.toBeInTheDocument());
  expect(screen.queryByLabelText('Beschreibung · Zeile 1')).not.toBeInTheDocument();
  rendered.rerender(<AllocationProposalPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Aufteilung erneut senden' })).toBeEnabled());
  expect(screen.getByLabelText('Beschreibung · Zeile 1')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Aufteilung erneut senden' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  for (const name of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[0].headers).get(name)).toBe(new Headers(requests[1].headers).get(name));
});

it('retains stale lines until the current context is explicitly adopted', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 409) : response(url)));
  render(<AllocationProposalPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Aufteilungsvorschlag speichern' }));
  await screen.findByText(/Die Aufteilung oder ihre Grundlage wurde geändert/);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Neue Aufteilungsgrundlage übernehmen' })).toBeEnabled());
  expect(screen.getByLabelText('Beschreibung · Zeile 1')).toHaveValue('Human reviewed service');
  fireEvent.click(screen.getByRole('button', { name: 'Neue Aufteilungsgrundlage übernehmen' }));
  expect(screen.getByLabelText('Beschreibung · Zeile 1')).toHaveValue(''); expect(screen.getByRole('checkbox')).not.toBeChecked();
});

it('does not expose a denied predecessor through a readable successor', async () => {
  let denied = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return json({}, 503);
    if (url.includes('/new-context/')) return response(url, { ...preview, context_decision_id: 'new-context', scope_label: 'New scope' });
    if (url.includes('allocation-preview') && denied) return json({}, 404);
    return response(url);
  }));
  const rendered = render(<AllocationProposalPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Aufteilungsvorschlag speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  denied = true; rendered.rerender(<AllocationProposalPanel {...props} contextDecisionId="new-context" parentGeneration={1} />);
  await screen.findByText(/New scope/); expect(screen.queryByText(/Synthetic scope/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Beschreibung · Zeile 1')).toHaveValue(''); expect(props.onDenied).not.toHaveBeenCalled();
});
