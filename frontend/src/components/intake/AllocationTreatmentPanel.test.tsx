import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { AllocationTreatmentPanel } from './AllocationTreatmentPanel';
import type { TreatmentPreview, TreatmentReview } from '../../api/allocationTreatments';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const preview: TreatmentPreview = { snapshot_digest: 'digest', can_review: true, snapshot: { allocation_proposal_id: 'allocation', scope_type: 'WEG', lines: [{ allocation_line_id: 'line', position: 1, gross_amount: '-59.00', vat_amount: null,
  category: { id: 'category', plan_version: 1, code: 'TEST', label: 'Test category', is_active: true, cost_group: 'GEMISCHT_PRUEFEN', reserve_relevant: false, tenant_apportionability_default: 'MANUELL_PRUEFEN', owner_billing_relevant_default: true, owner_distribution_key_default: 'MEA', tenant_distribution_key_default: 'WOHNFLAECHE' } }] } };
const empty = { items: [], next_before_revision: null };
const props = { logicalId: 'logical', allocationId: 'allocation', ready: true, parentBusy: false, parentGeneration: 0 };
function choice(label: string, value: string) { fireEvent.change(screen.getByLabelText(label + ' · Zeile 1'), { target: { value } }); }
async function fill() {
  await screen.findByLabelText('Aufwandsart · Zeile 1');
  choice('Aufwandsart', 'operating_expense'); choice('Steuerbehandlung', 'gross_without_input_vat'); choice('Mieteranteil', 'partial');
  choice('Exakter Mieterbetrag', '-10,00'); choice('Mieterverteilung', 'WOHNFLAECHE'); choice('Eigentümerverteilung', 'MEA');
  choice('Belegbezug der Behandlung', 'Explicit signed invoice line basis');
  fireEvent.change(screen.getByLabelText('Begründung der Behandlungsprüfung'), { target: { value: 'Human treatment reviewed' } });
  fireEvent.click(screen.getByRole('checkbox'));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('starts without defaults and validates exact negative partial cents with acknowledgment reset', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return json(url.includes('treatment-preview') ? preview : empty);
  }));
  render(<AllocationTreatmentPanel {...props} />);
  await screen.findByLabelText('Aufwandsart · Zeile 1');
  for (const label of ['Aufwandsart', 'Steuerbehandlung', 'Mieteranteil', 'Eigentümerverteilung']) expect(screen.getByLabelText(label + ' · Zeile 1')).toHaveValue('');
  await fill(); choice('Exakter Mieterbetrag', '10,00'); expect(screen.getByRole('checkbox')).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' }));
  await screen.findByText(/Bitte jede Behandlungsart/); expect(requests).toHaveLength(0);
  choice('Exakter Mieterbetrag', '-10,001'); fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' }));
  expect(requests).toHaveLength(0);
  choice('Exakter Mieterbetrag', '-10,00'); fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' }));
  await screen.findByText(/Keine bestätigte Antwort/);
  expect(JSON.parse(String(requests[0].body)).lines[0]).toEqual({ allocation_line_id: 'line', expense_intent: 'operating_expense', tax_intent: 'gross_without_input_vat', tenant_treatment: 'partial', tenant_amount: '-10.00', tenant_distribution_key: 'WOHNFLAECHE', owner_distribution_key: 'MEA', documentary_basis: 'Explicit signed invoice line basis' });
});

it('records deliberate unresolved values and MIET not-applicable without invented zero', async () => {
  const requests: RequestInit[] = []; const miet = { ...preview, snapshot: { ...preview.snapshot, scope_type: 'MIET' } };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return json(url.includes('treatment-preview') ? miet : empty); }));
  render(<AllocationTreatmentPanel {...props} />); await screen.findByLabelText('Aufwandsart · Zeile 1');
  choice('Aufwandsart', 'unresolved'); choice('Steuerbehandlung', 'unresolved'); choice('Mieteranteil', 'unresolved'); choice('Eigentümerverteilung', 'not_applicable'); choice('Belegbezug der Behandlung', 'Requires further document interpretation');
  fireEvent.change(screen.getByLabelText('Begründung der Behandlungsprüfung'), { target: { value: 'Unresolved questions recorded' } }); fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.queryByLabelText('Exakter Mieterbetrag · Zeile 1')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' })); await waitFor(() => expect(requests).toHaveLength(1));
  expect(JSON.parse(String(requests[0].body)).lines[0]).toMatchObject({ tenant_amount: null, tenant_distribution_key: null, owner_distribution_key: 'not_applicable' });
});

it('requires deliberate zero for none and clears obsolete amount/key on treatment change', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return json(url.includes('treatment-preview') ? preview : empty); }));
  render(<AllocationTreatmentPanel {...props} />); await fill(); choice('Mieteranteil', 'none');
  expect(screen.getByLabelText('Exakter Mieterbetrag · Zeile 1')).toHaveValue(''); expect(screen.queryByLabelText('Mieterverteilung · Zeile 1')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' })); await screen.findByText(/Bitte jede Behandlungsart/); expect(requests).toHaveLength(0);
  choice('Exakter Mieterbetrag', '0,00'); fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' })); await waitFor(() => expect(requests).toHaveLength(1));
  expect(JSON.parse(String(requests[0].body)).lines[0]).toMatchObject({ tenant_amount: '0.00', tenant_distribution_key: null });
});

it('retries the exact unknown command after committed history and a denied new allocation', async () => {
  const requests: RequestInit[] = []; let committed: TreatmentReview | null = null;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      requests.push(init); const body = JSON.parse(String(init.body));
      committed = { ...body, id: 'committed', logical_document_id: 'logical', allocation_proposal_id: 'allocation', previous_revision_id: null, revision_number: 1,
        financial_state: 'review_only', marker_policy: 'allocation-treatment-v1', snapshot: preview.snapshot, snapshot_digest: preview.snapshot_digest,
        actor_id: 'actor', created_at: '2026-10-03T07:00:00Z', lines: body.lines.map((line: object) => ({ ...line, id: 'stored-line', markers: [] })) };
      return json({}, 503);
    }
    if (url.includes('/allocation-proposals/new/')) return json({}, 404);
    return json(url.includes('treatment-preview') ? preview : { ...empty, items: committed ? [committed] : [] });
  }));
  const rendered = render(<AllocationTreatmentPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  rendered.rerender(<AllocationTreatmentPanel {...props} allocationId={null} ready={false} />);
  await waitFor(() => expect(screen.queryByLabelText('Aufwandsart · Zeile 1')).not.toBeInTheDocument());
  rendered.rerender(<AllocationTreatmentPanel {...props} allocationId="new" parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Behandlungsprüfung erneut senden' })).toBeEnabled());
  expect(screen.getByText(/Behandlungsprüfung · Version 1/)).toBeInTheDocument();
  expect(screen.getByLabelText('Aufwandsart · Zeile 1')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Behandlungsprüfung erneut senden' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  expect(JSON.parse(String(requests[1].body)).allocation_proposal_id).toBe('allocation');
  for (const name of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[0].headers).get(name)).toBe(new Headers(requests[1].headers).get(name));
});

it('does not automatically reuse treatment on a changed category snapshot', async () => {
  let current = preview;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('treatment-preview') ? current : empty)));
  const rendered = render(<AllocationTreatmentPanel {...props} />); await fill(); current = { ...preview, snapshot_digest: 'changed' };
  rendered.rerender(<AllocationTreatmentPanel {...props} parentGeneration={1} />);
  const reset = await screen.findByRole('button', { name: 'Neue Behandlungsgrundlage übernehmen' });
  expect(screen.getByLabelText('Exakter Mieterbetrag · Zeile 1')).toHaveValue('-10,00');
  expect(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' })).toBeDisabled();
  fireEvent.click(reset); expect(screen.getByLabelText('Aufwandsart · Zeile 1')).toHaveValue(''); expect(screen.getByRole('checkbox')).not.toBeChecked();
});

it('clears an inaccessible retained treatment and cannot resurrect its retry', async () => {
  let deny = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 503) : deny && url.includes('treatment-preview') ? json({}, 404) : json(url.includes('treatment-preview') ? preview : empty)));
  const rendered = render(<AllocationTreatmentPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Behandlungsprüfung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  deny = true; rendered.rerender(<AllocationTreatmentPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.queryByLabelText('Aufwandsart · Zeile 1')).not.toBeInTheDocument());
  deny = false; rendered.rerender(<AllocationTreatmentPanel {...props} parentGeneration={2} />);
  await screen.findByLabelText('Aufwandsart · Zeile 1'); expect(screen.getByLabelText('Aufwandsart · Zeile 1')).toHaveValue('');
  expect(screen.queryByRole('button', { name: 'Dieselbe Behandlungsprüfung erneut senden' })).not.toBeInTheDocument();
});
