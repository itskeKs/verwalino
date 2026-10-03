import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { AssignmentProposal, ExtractionRevision } from '../../api/intake';
import type { InvoiceReview, InvoiceReviewHistory } from '../../api/invoiceReviews';
import { InvoiceReviewPanel } from './InvoiceReviewPanel';

vi.mock('./ContextDecisionPanel', () => ({ ContextDecisionPanel: () => null }));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const proposal: AssignmentProposal = { id: 'proposal-1', document_ref_id: 'ref', logical_document_id: 'logical', partition_revision_id: 'partition', extraction_revision_id: 'extraction', scope_id: 'scope', scope_label: 'WEG Beispiel', scope_type: 'WEG', scope_hint_id: null, previous_proposal_id: null, revision_number: 1, method: 'manual_selection', state: 'blocked', blockers: ['scope_hint_conflict'], reason: 'Compare context', created_at: '2026-10-03T12:00:00Z' };
const extraction: ExtractionRevision = { id: 'extraction', document_ref_id: 'ref', logical_document_id: 'logical', created_at: proposal.created_at, status: 'succeeded', error_code: null, processor_version: 'intake-local-v1', payload: { vendor_name: 'Synthetic Utility', document_number: 'R-123', invoice_date: '2026-10-01', gross_amount: '119.00' }, fields: [] };
const empty: InvoiceReviewHistory = { items: [], next_before_revision: null, reviewable_assignment_id: proposal.id };
const review: InvoiceReview = { id: 'review-1', assignment_proposal_id: proposal.id, document_ref_id: 'ref', logical_document_id: 'logical', partition_revision_id: 'partition', extraction_revision_id: 'extraction', scope_id: 'scope', content_sha256: 'a'.repeat(64), previous_revision_id: null, revision_number: 1, seller_name: 'Reviewed Utility', document_number: 'R-123', document_kind: 'invoice', currency: 'EUR', invoice_date: '2026-10-01', service_period_from: '2026-09-01', service_period_to: '2026-09-30', gross_amount: '119.00', vat_amount: null, vat_rate: null, fields_reviewed: true, review_scope: 'invoice_fields', reason: 'Original compared manually', actor_id: 'actor', created_at: proposal.created_at, assignment_state: 'blocked', assignment_blockers: ['scope_hint_conflict'] };
const props = { logicalId: 'logical', proposal, extraction, ready: true, parentBusy: false, parentGeneration: 0, onDenied: vi.fn() };
async function fill() {
  await screen.findByLabelText('Belegart geprüft');
  fireEvent.change(screen.getByLabelText('Belegart geprüft'), { target: { value: 'invoice' } });
  fireEvent.change(screen.getByLabelText('Währung geprüft'), { target: { value: 'EUR' } });
  fireEvent.change(screen.getByLabelText('Leistungszeitraum von'), { target: { value: '2026-09-01' } });
  fireEvent.change(screen.getByLabelText('Leistungszeitraum bis'), { target: { value: '2026-09-30' } });
  fireEvent.change(screen.getByLabelText('Begründung der Feldprüfung'), { target: { value: 'Verified against original' } });
  fireEvent.click(screen.getByRole('checkbox'));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); props.onDenied.mockClear(); });

it('keeps type, currency and missing period explicit and resets acknowledgement after field edits', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(empty))); render(<InvoiceReviewPanel {...props} />);
  expect(await screen.findByLabelText('Aussteller geprüft')).toHaveValue('Synthetic Utility');
  expect(screen.getByLabelText('Belegart geprüft')).toHaveValue(''); expect(screen.getByLabelText('Währung geprüft')).toHaveValue('');
  expect(screen.getByLabelText('Leistungszeitraum von')).toHaveValue('');
  expect(screen.getByLabelText('Umsatzsteuerbetrag (optional)')).toHaveValue('');
  await fill(); expect(screen.getByRole('button', { name: 'Feldprüfung speichern' })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Bruttobetrag geprüft'), { target: { value: '120,00' } });
  expect(screen.getByRole('checkbox')).not.toBeChecked(); expect(screen.getByRole('button', { name: 'Feldprüfung speichern' })).toBeDisabled();
});

it('requires explicit credit signs and transmits exact decimal strings with unknown VAT', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return json(empty); }));
  render(<InvoiceReviewPanel {...props} />); await fill();
  fireEvent.change(screen.getByLabelText('Belegart geprüft'), { target: { value: 'credit_note' } }); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Feldprüfung speichern' })); await screen.findByText(/Gutschriften negative Beträge/); expect(requests).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('Bruttobetrag geprüft'), { target: { value: '-119,00' } }); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Feldprüfung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  expect(JSON.parse(String(requests[0].body))).toMatchObject({ gross_amount: '-119.00', vat_amount: null, vat_rate: null, document_kind: 'credit_note', fields_reviewed: true });
});

it('keeps an uncertain review frozen across missing parent context and failed read then replays its original basis', async () => {
  let offline = false; const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return offline ? json({}, 503) : json(empty); }));
  const rendered = render(<InvoiceReviewPanel {...props} />); await fill();
  fireEvent.click(screen.getByRole('button', { name: 'Feldprüfung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  offline = true; rendered.rerender(<InvoiceReviewPanel {...props} proposal={null} extraction={null} ready={false} parentBusy={true} />);
  await screen.findByText(/Feldprüfungen derzeit nicht verfügbar/); expect(screen.queryByLabelText('Begründung der Feldprüfung')).not.toBeInTheDocument();
  offline = false; rendered.rerender(<InvoiceReviewPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Feldprüfung erneut senden' })).toBeEnabled());
  expect(screen.getByLabelText('Begründung der Feldprüfung')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Feldprüfung erneut senden' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  for (const name of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[0].headers).get(name)).toBe(new Headers(requests[1].headers).get(name));
});

it('retains stale input until explicitly replacing it with the current human review', async () => {
  let advanced = false; const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); advanced = true; return json({}, 409); }
    return json(advanced ? { ...empty, items: [review] } : empty);
  }));
  render(<InvoiceReviewPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Feldprüfung speichern' }));
  await screen.findByText(/Menschlicher Prüfstand · Version 1/);
  expect(screen.getByLabelText('Begründung der Feldprüfung')).toHaveValue('Verified against original');
  expect(screen.getByRole('button', { name: 'Feldprüfung speichern' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Neue Feldgrundlage übernehmen' }));
  expect(screen.getByLabelText('Aussteller geprüft')).toHaveValue('Reviewed Utility'); expect(screen.getByLabelText('Begründung der Feldprüfung')).toHaveValue('');
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  fireEvent.change(screen.getByLabelText('Begründung der Feldprüfung'), { target: { value: 'Current basis compared' } }); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Feldprüfung speichern' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(new Headers(requests[1].headers).get('If-Match')).toBe('"review-1"');
});

it('preserves a blocked assignment in displayed review history and hides actions when read-only', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...empty, items: [review], reviewable_assignment_id: null }))); render(<InvoiceReviewPanel {...props} />);
  await screen.findByText('Der zugrunde liegende Zuordnungsvorschlag bleibt gesperrt.');
  expect(screen.getByText(/Umsatzsteuer: unbekannt · Satz: unbekannt/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Feldprüfung speichern' })).not.toBeInTheDocument();
});

it('drops private review history and pending inputs on source denial', async () => {
  let denied = false;
  vi.stubGlobal('fetch', vi.fn(async () => denied ? json({}, 404) : json({ ...empty, items: [review] })));
  render(<InvoiceReviewPanel {...props} />); await screen.findByText(/Menschlicher Prüfstand/); denied = true;
  fireEvent.click(screen.getByRole('button', { name: 'Feldprüfungen aktualisieren' })); await screen.findByText(/Feldprüfungen derzeit nicht verfügbar/);
  expect(props.onDenied).toHaveBeenCalledOnce(); expect(screen.queryByText('Original compared manually')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Aussteller geprüft')).not.toBeInTheDocument();
});
