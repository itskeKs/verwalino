import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DuplicateComparisonPanel } from './DuplicateComparisonPanel';
import type { DuplicateCandidate, DuplicatePage } from '../../api/duplicateComparisons';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const evidence = { normalization_version: 'pg16-lower-space-v1', match_reasons: ['same_bytes_and_range', 'same_reviewed_identity'] as DuplicateCandidate['evidence']['match_reasons'], kind_conflict: false,
  subject: { content_sha256: 'a'.repeat(64), page_start: 1, page_end: 1 }, reference: { content_sha256: 'a'.repeat(64), page_start: 1, page_end: 1 }, field_disagreements: ['vat_amount'] };
const candidate: DuplicateCandidate = { review_id: 'reference', logical_document_id: 'reference-logical', document_ref_id: 'reference-source', seller_name: 'Candidate utility', document_number: 'R-123', document_kind: 'invoice', gross_amount: '119.00', currency: 'EUR', can_compare: true, evidence, latest_comparison: null };
const page: DuplicatePage = { scope_id: 'scope', items: [candidate], next_cursor: null, financial_state: 'evidence_only' };
const props = { reviewId: 'subject', ready: true, parentBusy: false, parentGeneration: 0 };
function response(url: string, value = page) {
  if (url.includes('duplicate-candidates')) return json(value);
  if (url.includes('duplicate-comparisons')) return json({ items: [], next_before_revision: null });
  const id = url.split('/').at(-1);
  return json({ id, document_ref_id: `${id}-source`, logical_document_id: `${id}-logical`, extraction_revision_id: `${id}-extraction`, revision_number: 1,
    seller_name: `${id} utility`, document_number: 'R-123', document_kind: 'invoice', gross_amount: '119.00', currency: 'EUR', invoice_date: '2026-10-01', service_period_from: '2026-09-01', service_period_to: '2026-09-30', vat_amount: null, vat_rate: null });
}
async function select() { fireEvent.click(await screen.findByRole('button', { name: 'Belege gegenüberstellen · R-123' })); await screen.findByLabelText('Ergebnis des Belegvergleichs'); }
async function fill() {
  await select(); fireEvent.change(screen.getByLabelText('Ergebnis des Belegvergleichs'), { target: { value: 'same_document' } });
  fireEvent.change(screen.getByLabelText('Begründung des Belegvergleichs'), { target: { value: 'Both exact originals compared' } }); fireEvent.click(screen.getByRole('checkbox'));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('shows exact review evidence, requires choice and prevents distinct verdict on identical bytes', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => response(url))); render(<DuplicateComparisonPanel {...props} />); await select();
  expect(screen.getByLabelText('Ergebnis des Belegvergleichs')).toHaveValue(''); expect(screen.getByRole('button', { name: 'Belegvergleich speichern' })).toBeDisabled();
  expect(screen.queryByRole('option', { name: 'Verschiedene Belege' })).not.toBeInTheDocument();
  expect(screen.getByText(/Abweichende Felder: Umsatzsteuerbetrag/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Vergleichsbeleg im neuen Tab öffnen' })).toHaveAttribute('href', '/posteingang/belege/reference-source?logical_document_id=reference-logical&extraction_revision_id=reference-extraction');
  fireEvent.click(screen.getByRole('checkbox')); fireEvent.change(screen.getByLabelText('Begründung des Belegvergleichs'), { target: { value: 'Edited' } }); expect(screen.getByRole('checkbox')).not.toBeChecked();
});

it('freezes lost-response commands and replays the exact oriented pair after parent changes', async () => {
  const requests: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push({ url, init }); return json({}, 503); } return response(url); }));
  const rendered = render(<DuplicateComparisonPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Belegvergleich speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  rendered.rerender(<DuplicateComparisonPanel {...props} reviewId={null} ready={false} />);
  await waitFor(() => expect(screen.queryByLabelText('Begründung des Belegvergleichs')).not.toBeInTheDocument());
  rendered.rerender(<DuplicateComparisonPanel {...props} reviewId="new-subject" parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Denselben Vergleich erneut senden' })).toBeEnabled());
  expect(screen.getByLabelText('Begründung des Belegvergleichs')).toBeDisabled(); fireEvent.click(screen.getByRole('button', { name: 'Denselben Vergleich erneut senden' }));
  await waitFor(() => expect(requests).toHaveLength(2)); expect(requests[0].url).toBe('/api/invoice-reviews/subject/duplicate-comparisons'); expect(requests[1].url).toBe(requests[0].url);
  expect(requests[0].init.body).toBe(requests[1].init.body); for (const name of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[0].init.headers).get(name)).toBe(new Headers(requests[1].init.headers).get(name));
});

it('clears the retained private pair when its reference is denied despite readable current candidates', async () => {
  let deny = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 503) : deny && url === '/api/invoice-reviews/reference' ? json({}, 404) : response(url)));
  const rendered = render(<DuplicateComparisonPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Belegvergleich speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  deny = true; rendered.rerender(<DuplicateComparisonPanel {...props} reviewId="new-subject" parentGeneration={1} />);
  await screen.findByText(/Vergleichsquellen nicht mehr lesbar/); expect(screen.queryByLabelText('Begründung des Belegvergleichs')).not.toBeInTheDocument(); expect(screen.queryByText('reference utility · R-123')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Denselben Vergleich erneut senden' })).not.toBeInTheDocument();
});

it('preserves stale drafts for explicit discard and makes invoice-credit conflicts read-only', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 409) : response(url)));
  render(<DuplicateComparisonPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Belegvergleich speichern' }));
  await screen.findByText(/Schließen Sie diesen Entwurf/); expect(screen.getByLabelText('Begründung des Belegvergleichs')).toHaveValue('Both exact originals compared');
  await waitFor(() => expect(screen.getByRole('button', { name: 'Vergleichsentwurf schließen' })).toBeEnabled()); fireEvent.click(screen.getByRole('button', { name: 'Vergleichsentwurf schließen' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Belege gegenüberstellen · R-123' })).toBeEnabled());
  cleanup(); vi.stubGlobal('fetch', vi.fn(async (url: string) => response(url, { ...page, items: [{ ...candidate, evidence: { ...evidence, kind_conflict: true } }] })));
  render(<DuplicateComparisonPanel {...props} />); await select(); expect(screen.getByLabelText('Ergebnis des Belegvergleichs')).toBeDisabled(); expect(screen.getByText(/Zuerst die Feldprüfung korrigieren/)).toBeInTheDocument();
});

it('pages candidates without treating an empty visible page as clearance', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => { urls.push(url); return response(url, url.includes('&cursor=') ? { ...page, items: [] } : { ...page, next_cursor: 'signed-cursor' }); }));
  render(<DuplicateComparisonPanel {...props} />); fireEvent.click(await screen.findByRole('button', { name: 'Weitere Kandidaten' }));
  await screen.findByText('Keine lesbaren Kandidaten in dieser Suche.'); expect(screen.getByText(/Eine leere Liste bestätigt keine Dublettenfreiheit/)).toBeInTheDocument(); expect(urls.some(url => url.endsWith('&cursor=signed-cursor'))).toBe(true);
});

it('allows distinct and oriented replacement intent only for changed source bytes', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return response(url, { ...page, items: [{ ...candidate, evidence: { ...evidence, match_reasons: ['same_reviewed_identity'] } }] }); }));
  render(<DuplicateComparisonPanel {...props} />); await fill(); expect(screen.getByRole('option', { name: 'Verschiedene Belege' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Ergebnis des Belegvergleichs'), { target: { value: 'replacement_requested' } }); expect(screen.getByRole('checkbox')).not.toBeChecked(); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Belegvergleich speichern' })); await waitFor(() => expect(requests).toHaveLength(1));
  expect(JSON.parse(String(requests[0].body))).toEqual({ reference_review_id: 'reference', verdict: 'replacement_requested', reason: 'Both exact originals compared', comparison_reviewed: true });
});

it('keeps an authorized frozen pair when only the newer candidate context is denied', async () => {
  const requests: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push({ url, init }); return json({}, 503); }
    if (url.includes('/new-subject/duplicate-candidates')) return json({}, 404);
    return response(url);
  }));
  const rendered = render(<DuplicateComparisonPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Belegvergleich speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  rendered.rerender(<DuplicateComparisonPanel {...props} reviewId="new-subject" parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Denselben Vergleich erneut senden' })).toBeEnabled()); fireEvent.click(screen.getByRole('button', { name: 'Denselben Vergleich erneut senden' }));
  await waitFor(() => expect(requests).toHaveLength(2)); expect(requests[1].url).toBe(requests[0].url); expect(requests[1].init.body).toBe(requests[0].init.body);
  for (const name of ['If-Match', 'Idempotency-Key']) expect(new Headers(requests[1].init.headers).get(name)).toBe(new Headers(requests[0].init.headers).get(name));
});

it('states the direction of a reverse-oriented replacement in summary and retained history', async () => {
  const comparison = { id: 'comparison', scope_id: 'scope', subject_review_id: 'reference', reference_review_id: 'subject', subject_logical_id: 'reference-logical', reference_logical_id: 'subject-logical',
    previous_revision_id: null, revision_number: 1, verdict: 'replacement_requested' as const, financial_state: 'evidence_only' as const, evidence, reason: 'Reference replaces subject', actor_id: 'actor', created_at: '2026-10-03T12:00:00Z' };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('duplicate-comparisons') ? json({ items: [comparison], next_before_revision: null }) : response(url, { ...page, items: [{ ...candidate, latest_comparison: comparison }] })));
  render(<DuplicateComparisonPanel {...props} />); await select();
  expect(screen.getByText(/Letzter Paarvergleich: Vergleichsbeleg soll Ausgangsbeleg ersetzen/)).toBeInTheDocument();
  expect(screen.getByText(/Paarvergleich · Version 1 · Vergleichsbeleg soll Ausgangsbeleg ersetzen/)).toBeInTheDocument();
});
