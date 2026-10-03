import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { VisualDisclosurePanel } from './intake/VisualDisclosurePanel';
import { VisualDisclosureList } from './VisualDisclosureList';
import { VisualDisclosuresPage } from '../pages/VisualDisclosuresPage';
import type { VisualExtract } from '../api/visualExtracts';
import type { VisualDisclosure } from '../api/visualDisclosures';

const artifact: VisualExtract = { id: 'artifact', logical_document_id: 'logical', document_ref_id: 'ref', partition_revision_id: 'partition',
  page_start: 1, page_end: 1, output_page_count: 1, source_sha256: 'private-source', content_sha256: 'a'.repeat(64), content_byte_size: 100,
  reason: 'Private preparation reason', created_at: '2026-10-03T10:00:00Z' };
const destination = { id: 'scope', label: 'Selected destination', scope_type: 'WEG' };
const publication: VisualDisclosure = { destination, receipt: { id: 'published', kind: 'published', scope_id: 'scope', created_at: '2026-10-03T10:00:00Z',
  content_sha256: artifact.content_sha256, content_byte_size: 100, output_page_count: 1 },
  current: { id: 'published', kind: 'published', available: true, can_download: true, can_withdraw: true } };
const withdrawn: VisualDisclosure = { ...publication, receipt: { ...publication.receipt, id: 'withdrawn', kind: 'withdrawn' },
  current: { id: 'withdrawn', kind: 'withdrawn', available: false, can_download: false, can_withdraw: false } };
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const props = { artifact, canRead: true, canPublish: true, parentGeneration: 0, onPendingChange: vi.fn() };
function reads(url: string) {
  if (url.includes('/accounting-scopes/page')) return json({ items: [destination], next_cursor: null });
  if (url.includes('/disclosure-target')) return json({ current: null, partition_revision_id: 'partition', content_sha256: artifact.content_sha256, output_page_count: 1 });
  return json({ items: [], next_cursor: null });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });
async function prepare() {
  await screen.findByRole('option', { name: 'Selected destination · WEG' });
  fireEvent.change(screen.getByLabelText('Zielbereich der Freigabe'), { target: { value: 'scope' } });
  fireEvent.click(await screen.findByRole('link', { name: 'Genau diesen Auszug für den Zielbereich öffnen' }));
  fireEvent.change(screen.getByLabelText('Begründung der Offenlegung'), { target: { value: 'Visible content reviewed for chosen audience' } });
  fireEvent.click(screen.getByLabelText(/Alle sichtbaren Inhalte dieses Auszugs/));
}

it('requires selecting the destination and opening the exact artifact before acknowledgment', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json(publication, 201); }
    return reads(url);
  }));
  render(<MemoryRouter><VisualDisclosurePanel {...props} /></MemoryRouter>);
  expect(screen.getByRole('button', { name: 'Geprüften Auszug freigeben' })).toBeDisabled();
  expect(screen.getByLabelText(/Alle sichtbaren Inhalte dieses Auszugs/)).toBeDisabled();
  await prepare(); fireEvent.click(screen.getByRole('button', { name: 'Geprüften Auszug freigeben' }));
  await screen.findByText(/Dieser Auszug ist derzeit freigegeben/);
  expect(JSON.parse(String(requests[0].body))).toEqual({ scope_id: 'scope', partition_revision_id: 'partition', content_sha256: artifact.content_sha256,
    reason: 'Visible content reviewed for chosen audience', visible_content_reviewed: true });
  expect(new Headers(requests[0].headers).get('If-Match')).toBe('"none"');
});

it('keeps an unknown publication exact through parent refresh and labels a withdrawn historical receipt', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return requests.length === 1 ? json({}, 503) : json({ ...publication, current: withdrawn.current }, 201); }
    return reads(url);
  }));
  const view = render(<MemoryRouter><VisualDisclosurePanel {...props} /></MemoryRouter>); await prepare();
  fireEvent.click(screen.getByRole('button', { name: 'Geprüften Auszug freigeben' })); await screen.findByText(/Keine bestätigte Antwort/);
  view.rerender(<MemoryRouter><VisualDisclosurePanel {...props} canRead={false} parentGeneration={1} /></MemoryRouter>);
  expect(screen.queryByLabelText('Begründung der Offenlegung')).not.toBeInTheDocument();
  view.rerender(<MemoryRouter><VisualDisclosurePanel {...props} parentGeneration={2} /></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Dieselbe Freigabe erneut senden' }));
  await screen.findByText(/Bestätigung ist historisch/);
  expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
});

it('retains a lost withdrawal after a refreshed destination list no longer contains it', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return requests.length === 1 ? json({}, 503) : json(withdrawn, 201); }
    return json({ items: requests.length ? [] : [publication], next_cursor: null });
  }));
  render(<VisualDisclosureList endpoint="/api/accounting-scopes/scope/visual-disclosures" />);
  fireEvent.click(await screen.findByRole('button', { name: 'Freigabe widerrufen' }));
  fireEvent.change(screen.getByLabelText('Grund des Widerrufs'), { target: { value: 'Withdraw this destination' } });
  fireEvent.click(screen.getByLabelText('Weitere Abrufe dieser Freigabe ausdrücklich sperren.'));
  fireEvent.click(screen.getByRole('button', { name: 'Widerruf bestätigen' })); await screen.findByText(/Keine bestätigte Antwort/);
  fireEvent.click(screen.getByRole('button', { name: 'Freigaben neu laden' })); await screen.findByText('Keine Freigaben auf dieser Ergebnisseite.');
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Widerruf erneut senden' })); await screen.findByText(/Widerruf gespeichert/);
  expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
  expect(screen.queryByRole('link', { name: 'Freigegebenen Auszug öffnen' })).not.toBeInTheDocument();
});

it('clears destination links on denied refresh and does not expose source provenance', async () => {
  let deny = false;
  vi.stubGlobal('fetch', vi.fn(async () => deny ? json({}, 404) : json({ items: [publication], next_cursor: null })));
  render(<VisualDisclosureList endpoint="/api/accounting-scopes/scope/visual-disclosures" />);
  await screen.findByRole('link', { name: 'Freigegebenen Auszug öffnen' }); deny = true;
  fireEvent.click(screen.getByRole('button', { name: 'Freigaben neu laden' })); await screen.findByRole('alert');
  expect(screen.queryByRole('link', { name: 'Freigegebenen Auszug öffnen' })).not.toBeInTheDocument();
  expect(screen.queryByText(artifact.reason)).not.toBeInTheDocument();
});

it('makes outgoing withdrawal management reachable without a destination selection', async () => {
  const fetch = vi.fn(async (url: string) => url.includes('outgoing') ? json({ items: [{ ...publication, destination: null, receipt: { ...publication.receipt, scope_id: null }, current: { ...publication.current, can_download: false } }], next_cursor: null }) : reads(url));
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><VisualDisclosuresPage /></MemoryRouter>);
  fireEvent.click(screen.getByLabelText('Meine ausgehenden Freigaben'));
  await screen.findByText(/Zielbereich derzeit nicht lesbar/);
  expect(screen.getByRole('button', { name: 'Freigabe widerrufen' })).toBeEnabled();
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => String(url).includes('/outgoing'))).toBe(true));
});

it.each([404, 409, 503])('handles an uncertain publication followed by target status %s', async status => {
  let failed = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { failed = true; return json({}, 503); }
    if (failed && url.includes('/disclosure-target')) return json({}, status);
    return reads(url);
  }));
  render(<MemoryRouter><VisualDisclosurePanel {...props} /></MemoryRouter>); await prepare();
  fireEvent.click(screen.getByRole('button', { name: 'Geprüften Auszug freigeben' })); await screen.findByText(/Keine bestätigte Antwort/);
  fireEvent.click(screen.getByRole('button', { name: 'Freigabegrundlage neu prüfen' }));
  await screen.findByText(/Freigabegrundlage nicht verfügbar/);
  if (status === 404) {
    expect(screen.queryByRole('button', { name: 'Dieselbe Freigabe erneut senden' })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Zielbereich der Freigabe')).toHaveValue('');
    expect(screen.getByLabelText('Begründung der Offenlegung')).toHaveValue('');
  } else expect(screen.getByRole('button', { name: 'Dieselbe Freigabe erneut senden' })).toBeEnabled();
});

it('refreshes the publisher status after a confirmed withdrawal below it', async () => {
  let state: VisualDisclosure | null = null;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { state = url.endsWith('/withdrawals') ? withdrawn : publication; return json(state, 201); }
    if (url.includes('/disclosure-target')) return json({ current: state, partition_revision_id: 'partition', content_sha256: artifact.content_sha256, output_page_count: 1 });
    if (url.includes('/disclosures?')) return json({ items: state ? [state] : [], next_cursor: null });
    return reads(url);
  }));
  render(<MemoryRouter><VisualDisclosurePanel {...props} /></MemoryRouter>); await prepare();
  fireEvent.click(screen.getByRole('button', { name: 'Geprüften Auszug freigeben' })); await screen.findByText(/Dieser Auszug ist derzeit freigegeben/);
  fireEvent.click(await screen.findByRole('button', { name: 'Freigabe widerrufen' }));
  fireEvent.change(screen.getByLabelText('Grund des Widerrufs'), { target: { value: 'Withdraw after publication' } });
  fireEvent.click(screen.getByLabelText('Weitere Abrufe dieser Freigabe ausdrücklich sperren.'));
  fireEvent.click(screen.getByRole('button', { name: 'Widerruf bestätigen' }));
  await waitFor(() => expect(screen.queryByText(/Dieser Auszug ist derzeit freigegeben/)).not.toBeInTheDocument());
  await screen.findByRole('link', { name: 'Genau diesen Auszug für den Zielbereich öffnen' });
  expect(screen.queryByText(/bereits freigegeben/)).not.toBeInTheDocument();
});
