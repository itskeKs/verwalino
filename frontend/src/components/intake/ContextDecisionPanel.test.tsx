import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ContextPreview } from '../../api/contextDecisions';
import { ContextDecisionPanel } from './ContextDecisionPanel';

vi.mock('./AllocationProposalPanel', () => ({ AllocationProposalPanel: () => null }));

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const preview: ContextPreview = { logical_document_id: 'logical', assignment_proposal_id: 'proposal', invoice_review_id: 'review', case_id: 'case', dependency_id: 'dep', case_event_id: 'event', snapshot_digest: 'a'.repeat(64), page_start: 2, page_end: 3, can_review: true,
  snapshot: { property: { id: 'property', name: 'Reviewed building', street: 'Synthetic street 4', zip_code: '10115', city: 'Berlin' }, scope: { id: 'scope', property_id: 'property', management_mandate_id: null, scope_type: 'WEG', label: 'WEG 2026', valid_from: '2026-01-01', valid_to: null, whole_property: true } } };
const empty = { items: [], next_before_revision: null };
const props = { logicalId: 'logical', proposalId: 'proposal', reviewId: 'review', ready: true, parentBusy: false, parentGeneration: 0, onDenied: vi.fn() };
async function fill() {
  await screen.findByLabelText('Seite der Objektangabe');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Kontextprüfungen aktualisieren' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Seite der Objektangabe'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('Zitat zur Objektzuordnung'), { target: { value: 'Original property address' } });
  fireEvent.change(screen.getByLabelText('Begründung des Kontextvergleichs'), { target: { value: 'Compared the destination address' } });
  fireEvent.click(screen.getByRole('checkbox'));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); props.onDenied.mockClear(); });

it('requires explicit source comparison and clears acknowledgement after edits', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('context-preview') ? preview : empty)));
  render(<ContextDecisionPanel {...props} />); await fill();
  expect(screen.getByText(/Synthetic street 4/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Kontextprüfung speichern' })).toBeEnabled();
  fireEvent.change(screen.getByLabelText('Zitat zur Objektzuordnung'), { target: { value: 'Correction' } });
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});

it('validates the selected page range and freezes an uncertain command across parent refreshes', async () => {
  const requests: RequestInit[] = []; let offline = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json({}, 503); }
    return offline ? json({}, 503) : json(url.includes('context-preview') ? preview : empty);
  }));
  const rendered = render(<ContextDecisionPanel {...props} />); await fill();
  fireEvent.change(screen.getByLabelText('Seite der Objektangabe'), { target: { value: '1' } }); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Kontextprüfung speichern' })); await screen.findByText(/Bitte Fundstelle innerhalb/); expect(requests).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('Seite der Objektangabe'), { target: { value: '2' } }); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Kontextprüfung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  expect(JSON.parse(String(requests[0].body))).toMatchObject({ invoice_review_id: 'review', assignment_proposal_id: 'proposal', expected_case_event_id: 'event', snapshot_digest: preview.snapshot_digest, page_number: 2, xml_path: null });
  offline = true; rendered.rerender(<ContextDecisionPanel {...props} proposalId={null} reviewId={null} parentBusy ready={false} />);
  await screen.findByText(/Kontextprüfungen derzeit nicht verfügbar/); expect(screen.queryByText(/Synthetic street 4/)).not.toBeInTheDocument();
  offline = false; rendered.rerender(<ContextDecisionPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Kontextprüfung erneut senden' })).toBeEnabled());
  expect(screen.getByLabelText('Zitat zur Objektzuordnung')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Kontextprüfung erneut senden' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  for (const name of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[0].headers).get(name)).toBe(new Headers(requests[1].headers).get(name));
});

it('preserves stale entries until the new property snapshot is explicitly adopted', async () => {
  let advanced = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { advanced = true; return json({}, 409); }
    return json(url.includes('context-preview') ? { ...preview, snapshot_digest: advanced ? 'b'.repeat(64) : preview.snapshot_digest } : empty);
  }));
  render(<ContextDecisionPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Kontextprüfung speichern' }));
  await screen.findByText(/Die Kontextgrundlage wurde geändert oder wird gerade bearbeitet/);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Neue Kontextgrundlage übernehmen' })).toBeEnabled());
  expect(screen.getByLabelText('Zitat zur Objektzuordnung')).toHaveValue('Original property address');
  expect(screen.getByRole('button', { name: 'Kontextprüfung speichern' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Neue Kontextgrundlage übernehmen' }));
  expect(screen.getByLabelText('Zitat zur Objektzuordnung')).toHaveValue(''); expect(screen.getByRole('checkbox')).not.toBeChecked();
});

it('clears copied property facts and pending text after a permission denial', async () => {
  let denied = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => denied ? json({}, 404) : json(url.includes('context-preview') ? preview : empty)));
  render(<ContextDecisionPanel {...props} />); await fill(); denied = true;
  fireEvent.click(screen.getByRole('button', { name: 'Kontextprüfungen aktualisieren' })); await screen.findByText(/Kontextprüfungen derzeit nicht verfügbar/);
  expect(props.onDenied).toHaveBeenCalled(); expect(screen.queryByText(/Synthetic street 4/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Zitat zur Objektzuordnung')).not.toBeInTheDocument();
});

it('labels XML paths as human assertions and blocks unsupported scopes', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('context-preview') ? { ...preview, page_start: null, page_end: null } : empty)));
  const rendered = render(<ContextDecisionPanel {...props} />);
  await screen.findByLabelText('Angegebener XML-Pfad'); expect(screen.getByText(/nicht automatisch gegen das XML/)).toBeInTheDocument();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('context-preview') ? { ...preview, snapshot: { ...preview.snapshot, scope: { ...preview.snapshot.scope, scope_type: 'SEV', whole_property: false } } } : empty)));
  rendered.rerender(<ContextDecisionPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Kontextprüfung speichern' })).not.toBeInTheDocument());
});

it('hides property-only denial without declaring the original inaccessible', async () => {
  let denied = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('context-preview') ? denied ? json({}, 404) : json(preview) : json(empty)));
  render(<ContextDecisionPanel {...props} />); await fill(); denied = true;
  fireEvent.click(screen.getByRole('button', { name: 'Kontextprüfungen aktualisieren' })); await screen.findByText(/Kontextprüfungen derzeit nicht verfügbar/);
  expect(props.onDenied).not.toHaveBeenCalled(); expect(screen.queryByText(/Synthetic street 4/)).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Zitat zur Objektzuordnung')).not.toBeInTheDocument();
});

it('keeps an unknown command private when parent context disappears but history is readable', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json({}, 503); }
    return json(url.includes('context-preview') ? preview : empty);
  }));
  const rendered = render(<ContextDecisionPanel {...props} />); await fill();
  fireEvent.click(screen.getByRole('button', { name: 'Kontextprüfung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  rendered.rerender(<ContextDecisionPanel {...props} proposalId={null} reviewId={null} ready={false} />);
  await waitFor(() => expect(screen.queryByText(/Synthetic street 4/)).not.toBeInTheDocument());
  expect(screen.queryByLabelText('Zitat zur Objektzuordnung')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Dieselbe Kontextprüfung erneut senden' })).not.toBeInTheDocument();
  rendered.rerender(<ContextDecisionPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Kontextprüfung erneut senden' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Kontextprüfung erneut senden' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
});

it('does not let a readable successor authorize a denied frozen predecessor', async () => {
  let revoked = false;
  const next = { ...preview, assignment_proposal_id: 'proposal-B', invoice_review_id: 'review-B', snapshot: { ...preview.snapshot, property: { ...preview.snapshot.property, name: 'Other building', street: 'Other street' } } };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') return json({}, 503);
    if (url.includes('proposal-B/context-preview')) return json(next);
    if (url.includes('context-preview')) return revoked ? json({}, 404) : json(preview);
    return json(empty);
  }));
  const rendered = render(<ContextDecisionPanel {...props} />); await fill();
  fireEvent.click(screen.getByRole('button', { name: 'Kontextprüfung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  revoked = true; rendered.rerender(<ContextDecisionPanel {...props} proposalId="proposal-B" reviewId="review-B" parentGeneration={1} />);
  await screen.findByText(/Other street/); expect(screen.queryByText(/Synthetic street 4/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Zitat zur Objektzuordnung')).toHaveValue('');
  expect(screen.queryByRole('button', { name: 'Dieselbe Kontextprüfung erneut senden' })).not.toBeInTheDocument();
  expect(props.onDenied).not.toHaveBeenCalled();
});
