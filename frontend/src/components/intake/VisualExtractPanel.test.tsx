import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { VisualExtractPanel } from './VisualExtractPanel';
import type { LogicalDocument } from '../../api/intake';

const document: LogicalDocument = { id: 'logical', document_ref_id: 'ref', page_start: 1, page_end: 2,
  verified_page_count: 3, origin: 'inspection', review_state: 'unreviewed', reason: 'Fixture' };
const receipt = { id: 'extract', logical_document_id: 'logical', partition_revision_id: 'partition', page_start: 1, page_end: 2,
  output_page_count: 2, content_sha256: 'a'.repeat(64), reason: 'Private preparation', created_at: '2026-10-03T10:00:00Z' };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const props = { documents: [document], partitionId: 'partition', memberIds: ['logical'], canRead: true, canGenerate: true, disabled: false, parentGeneration: 0 };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
function choose() {
  fireEvent.change(screen.getByLabelText('Beleg für den Auszug'), { target: { value: 'logical' } });
  fireEvent.change(screen.getByLabelText('Grund für den Auszug'), { target: { value: 'Private preparation' } });
}

it('requires current membership and explicit reason, then links the stored bytes', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json(receipt, 201); }
    return json({ items: [], next_cursor: null });
  }));
  render(<VisualExtractPanel {...props} />);
  expect(screen.getByRole('button', { name: 'Privaten Auszug erzeugen' })).toBeDisabled();
  choose(); fireEvent.click(screen.getByRole('button', { name: 'Privaten Auszug erzeugen' }));
  expect(await screen.findByRole('link', { name: 'Erzeugten Auszug öffnen' })).toHaveAttribute('href', '/api/visual-extracts/extract/content');
  expect(JSON.parse(String(requests[0].body))).toEqual({ partition_revision_id: 'partition', reason: 'Private preparation' });
  expect(screen.getByLabelText('Grund für den Auszug')).toHaveValue('');
});

it('preserves the exact uncertain command across a newer partition', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return requests.length === 1 ? json({}, 503) : json(receipt, 201); }
    return json({ items: [], next_cursor: null });
  }));
  const view = render(<VisualExtractPanel {...props} />); choose();
  fireEvent.click(screen.getByRole('button', { name: 'Privaten Auszug erzeugen' }));
  await screen.findByText(/Keine bestätigte Antwort/);
  view.rerender(<VisualExtractPanel {...props} partitionId="new-partition" memberIds={[]} />);
  expect(screen.getByLabelText('Beleg für den Auszug')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Auszugsanforderung erneut senden' }));
  await screen.findByRole('link', { name: 'Erzeugten Auszug öffnen' });
  expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
  expect(screen.getByRole('button', { name: 'Privaten Auszug erzeugen' })).toBeDisabled();
});

it('clears sensitive output on denial and keeps history failures retryable', async () => {
  let fail = false;
  vi.stubGlobal('fetch', vi.fn(async () => fail ? json({}, 404) : json({ items: [receipt], next_cursor: null })));
  render(<VisualExtractPanel {...props} />); choose();
  await screen.findByRole('link', { name: 'Seiten 1–2 öffnen' }); fail = true;
  fireEvent.click(screen.getByRole('button', { name: 'Auszüge neu laden' }));
  await screen.findByText(/Kein Zugriff auf private Auszüge/);
  expect(screen.queryByRole('link', { name: 'Seiten 1–2 öffnen' })).not.toBeInTheDocument();
});

it('does not request history without export and clears cached details when export is revoked', async () => {
  const fetch = vi.fn(async () => json({ items: [receipt], next_cursor: null })); vi.stubGlobal('fetch', fetch);
  const view = render(<VisualExtractPanel {...props} canRead={false} canGenerate={false} />);
  expect(screen.getByLabelText('Beleg für den Auszug')).toBeDisabled(); expect(fetch).not.toHaveBeenCalled();
  view.rerender(<VisualExtractPanel {...props} />); choose(); await screen.findByRole('link', { name: 'Seiten 1–2 öffnen' });
  view.rerender(<VisualExtractPanel {...props} canRead={false} canGenerate={false} parentGeneration={1} />);
  expect(screen.queryByRole('link', { name: 'Seiten 1–2 öffnen' })).not.toBeInTheDocument();
  expect(screen.queryByText('Private preparation')).not.toBeInTheDocument();
});

it('reauthorizes retained details on a parent refresh', async () => {
  const fetch = vi.fn(async () => json({ items: [receipt], next_cursor: null })); vi.stubGlobal('fetch', fetch);
  const view = render(<VisualExtractPanel {...props} />); choose(); await screen.findByRole('link', { name: 'Seiten 1–2 öffnen' });
  view.rerender(<VisualExtractPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
});

it('allows retained history but prevents generation for a former member', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ items: [receipt], next_cursor: null })));
  render(<VisualExtractPanel {...props} memberIds={[]} partitionId="new-partition" />); choose();
  await screen.findByRole('link', { name: 'Seiten 1–2 öffnen' });
  expect(screen.getByText(/frühere Aufteilung/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Privaten Auszug erzeugen' })).toBeDisabled();
});
