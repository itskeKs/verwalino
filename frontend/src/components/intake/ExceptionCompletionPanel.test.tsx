import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ExceptionCompletionPanel } from './ExceptionCompletionPanel';
import type { CompletionPreview, CompletionReceipt, ExceptionCase } from '../../api/exceptions';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const item: ExceptionCase = { id: 'case', document_ref_id: 'ref', logical_document_id: 'logical', extraction_revision_id: 'extraction', scope_id: 'scope', scope_label: 'WEG Test', scope_type: 'WEG', cause: 'duplicate_candidate', created_at: '2026-10-03T10:00:00Z', updated_at: '2026-10-03T10:00:00Z', revision_id: 'event', revision_number: 1, resolution_state: 'unresolved', workflow_state: 'open', claimant_id: null, snoozed_until: null, dependency_count: 1, can_work: true };
const preview: CompletionPreview = { can_complete: true, expected_event_id: 'event', preview_digest: 'digest', blocker: null,
  members: [1, 2, 3].map(n => ({ logical_document_id: `logical-${n}`, document_ref_id: `ref-${n}`, invoice_review_id: `review-${n}`, allocation_set_id: 'set', disposition: n === 1 ? 'adopted_document' : 'covered_copy', source_document_id: 'source', document_number: `R-${n}`, gross_amount: '119.00', cost_count: 2 })),
  comparisons: [[1, 2], [1, 3], [2, 3]].map(([a, b]) => ({ left_logical_id: `logical-${a}`, right_logical_id: `logical-${b}`, comparison_id: `comparison-${a}-${b}`, verdict: 'same_document', subject_review_id: `review-${a}`, reference_review_id: `review-${b}` })),
  dispositions: [{ dependency_id: 'dep', kind: 'duplicate', disposition: 'superseded_by_current_comparison', comparison_id: 'comparison-1-2' }] };
const receipt: CompletionReceipt = { id: 'receipt', case_id: 'case', basis_digest: 'digest', reason: 'All outcomes checked', created_at: item.created_at, ...preview };
const props = { caseId: 'case', item, parentBusy: false, parentGeneration: 0, onCompleted: vi.fn() };
function panel(overrides: Partial<typeof props> & { selectedReceiptId?: string } = {}) { return <MemoryRouter><ExceptionCompletionPanel {...props} {...overrides} /></MemoryRouter>; }
async function fill() {
  await screen.findByLabelText('Begründung des Abschlusses');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Abschlussprüfung aktualisieren' })).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Begründung des Abschlusses'), { target: { value: 'All outcomes checked' } });
  fireEvent.click(screen.getByRole('checkbox', { name: /Alle Belege, Vergleiche/ }));
}
async function refreshed() { await waitFor(() => expect(screen.getByRole('button', { name: 'Abschlussprüfung aktualisieren' })).toBeEnabled()); }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('shows every connected source, pair and retained disposition and requires explicit acknowledgement', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json(receipt, 201); } return json(url.endsWith('/case') ? item : preview); }));
  render(panel()); await screen.findByLabelText('Begründung des Abschlusses');
  expect(screen.getByText('Beleg 2 und Beleg 3: Derselbe Beleg')).toBeInTheDocument();
  expect(screen.getByText(/Durch aktuellen Belegvergleich ersetzt/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Beleg 3 prüfen' })).toHaveAttribute('href', '/posteingang/belege/ref-3?logical_document_id=logical-3');
  expect(screen.getByRole('button', { name: 'Geprüften Fall abschließen' })).toBeDisabled();
  await fill(); fireEvent.change(screen.getByLabelText('Begründung des Abschlusses'), { target: { value: 'Changed reason' } });
  expect(screen.getByRole('checkbox')).not.toBeChecked();
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.click(screen.getByRole('button', { name: 'Geprüften Fall abschließen' }));
  await waitFor(() => expect(props.onCompleted).toHaveBeenCalled());
  expect(JSON.parse(String(requests[0].body))).toEqual({ preview_digest: 'digest', reason: 'Changed reason', completion_reviewed: true });
  expect(new Headers(requests[0].headers).get('If-Match')).toBe('"event"');
});
it('preserves an unknown command through parent unavailability, changed basis and a recorded completion', async () => {
  const requests: RequestInit[] = []; let current = preview;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return json(url.endsWith('/case') ? { ...item, completion_id: 'receipt', completion_changed: current.preview_digest !== 'digest' } : url.includes('/completions/') ? receipt : current); }));
  const rendered = render(panel()); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Geprüften Fall abschließen' })); await screen.findByText(/Keine bestätigte Antwort/);
  rendered.rerender(<MemoryRouter><ExceptionCompletionPanel {...props} item={null} parentBusy parentGeneration={1} /></MemoryRouter>);
  expect(screen.queryByLabelText('Begründung des Abschlusses')).not.toBeInTheDocument();
  current = { ...preview, can_complete: false, preview_digest: null, members: [], comparisons: [], dispositions: [], expected_event_id: 'new-event' };
  rendered.rerender(panel({ item: { ...item, completion_id: 'receipt', completion_changed: true }, parentGeneration: 2 }));
  await refreshed(); expect(screen.getByLabelText('Begründung des Abschlusses')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Abschluss erneut senden' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  for (const header of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[0].headers).get(header)).toBe(new Headers(requests[1].headers).get(header));
  expect(screen.getByText(/Neue oder geänderte Belege/)).toBeInTheDocument();
});
it('requires fresh reason and acknowledgement after a changed exact basis', async () => {
  let current = preview; vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.endsWith('/case') ? item : current)));
  const rendered = render(panel()); await fill(); current = { ...preview, preview_digest: 'new' };
  rendered.rerender(panel({ parentGeneration: 1 })); await refreshed();
  expect(screen.getByRole('button', { name: 'Geprüften Fall abschließen' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Aktuelle Abschlussgrundlage übernehmen' }));
  expect(screen.getByLabelText('Begründung des Abschlusses')).toHaveValue(''); expect(screen.getByRole('checkbox')).not.toBeChecked();
});
it('clears private drafts and pending retries after retained authority loss', async () => {
  let deny = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 503) : deny ? json({ ...preview, can_complete: false, preview_digest: null, members: [], comparisons: [], dispositions: [] }, 404) : json(url.endsWith('/case') ? item : preview)));
  const rendered = render(panel()); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Geprüften Fall abschließen' })); await screen.findByText(/Keine bestätigte Antwort/);
  deny = true; rendered.rerender(panel({ parentGeneration: 1 })); await refreshed();
  expect(screen.queryByLabelText('Begründung des Abschlusses')).not.toBeInTheDocument(); expect(screen.queryByText(/R-3/)).not.toBeInTheDocument();
  deny = false; rendered.rerender(panel({ parentGeneration: 2 })); await refreshed();
  expect(screen.getByLabelText('Begründung des Abschlusses')).toHaveValue('');
  expect(screen.queryByRole('button', { name: 'Denselben Abschluss erneut senden' })).not.toBeInTheDocument();
});
it('shows a historical receipt without declaring the reopened case completed', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.endsWith('/case') ? item : url.includes('/completions/') ? receipt : { ...preview, can_complete: false })));
  render(panel({ selectedReceiptId: 'receipt' })); await refreshed();
  fireEvent.click(screen.getByText(/Gespeicherter Abschluss vom/));
  expect(screen.getByText(/Historischer Abschlussbeleg; maßgeblich/)).toBeInTheDocument();
  expect(screen.queryByText('Der gespeicherte Abschluss gilt für den aktuellen Stand.')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Geprüften Fall abschließen' })).not.toBeInTheDocument();
});

it('refreshes effective validity independently of stale completed parent data', async () => {
  let changed = false;
  const completed: ExceptionCase = { ...item, completion_id: 'receipt', completion_changed: false, workflow_state: 'completed', resolution_state: 'resolved' };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.endsWith('/case') ? changed ? { ...completed, completion_changed: true, workflow_state: 'open', resolution_state: 'unresolved' } : completed : url.includes('/completions/') ? receipt : { ...preview, can_complete: !changed })));
  render(panel({ item: completed })); await refreshed(); fireEvent.click(screen.getByText(/Gespeicherter Abschluss vom/));
  expect(screen.getByText('Der gespeicherte Abschluss gilt für den aktuellen Stand.')).toBeInTheDocument();
  changed = true; fireEvent.click(screen.getByRole('button', { name: 'Abschlussprüfung aktualisieren' })); await refreshed();
  expect(screen.queryByText('Der gespeicherte Abschluss gilt für den aktuellen Stand.')).not.toBeInTheDocument();
  expect(screen.getByText('Die Grundlage hat sich geändert. Der Fall ist wieder offen.')).toBeInTheDocument();
});
it('clears an unknown request when a retained member is denied although the case is still readable', async () => {
  let revoked = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 503) : revoked && url.includes('/invoice-reviews/') ? json({}, 404) : json(url.endsWith('/case') ? item : revoked ? { ...preview, can_complete: false, preview_digest: null, members: [], comparisons: [], dispositions: [] } : preview)));
  const rendered = render(panel()); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Geprüften Fall abschließen' })); await screen.findByText(/Keine bestätigte Antwort/);
  revoked = true; rendered.rerender(panel({ parentGeneration: 1 })); await refreshed();
  expect(screen.queryByRole('button', { name: 'Denselben Abschluss erneut senden' })).not.toBeInTheDocument();
  revoked = false; rendered.rerender(panel({ parentGeneration: 2 })); await refreshed();
  expect(screen.getByLabelText('Begründung des Abschlusses')).toHaveValue('');
});
