import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SourcePartitionPanel } from './SourcePartitionPanel';
import type { LogicalDocument, SourcePartition } from '../../api/intake';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const whole: LogicalDocument = { id: 'whole', document_ref_id: 'ref', page_start: 1, page_end: 3, verified_page_count: 3, origin: 'inspection', review_state: 'unreviewed', reason: 'Whole' };
const first = { ...whole, id: 'first', page_end: 1 };
const rest = { ...whole, id: 'rest', page_start: 2 };
const revision: SourcePartition = { id: 'revision-1', document_ref_id: 'ref', previous_revision_id: null, revision_number: 1, content_sha256: 'hash',
  verified_page_count: 3, visual_pages_reviewed: true, reason: 'Prior review', created_at: '2026-10-03T12:00:00Z', logical_document_ids: ['whole'] };
function page(onDenied = vi.fn()) { return render(<SourcePartitionPanel refId="ref" documents={[whole, first, rest]} parentBusy={false} parentGeneration={0} onDenied={onDenied} onCreated={vi.fn()} />); }
function respond(url: string) { return url.endsWith('/partition') ? json({ current: null, can_review: true, can_export: true }) : json({ source_kind: 'intake_original', source_id: 'original' }); }
function complete() {
  fireEvent.click(screen.getByLabelText('Seite 1 in Aufteilung aufnehmen'));
  fireEvent.click(screen.getByLabelText('Seiten 2–3 in Aufteilung aufnehmen'));
  fireEvent.change(screen.getByLabelText('Begründung der Aufteilung'), { target: { value: 'Zwei getrennte Rechnungen gesichtet' } });
  fireEvent.click(screen.getByLabelText(/Alle sichtbaren Seiten im Original geprüft/));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('requires exact coverage and sends an explicit visual review with predecessor', async () => {
  let request: RequestInit | undefined;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { request = init; return json({ ...revision, logical_document_ids: ['first', 'rest'] }, 201); }
    return respond(url);
  }));
  page(); await screen.findByText('Noch keine geprüfte Aufteilung.');
  expect(screen.getByRole('link', { name: 'Original zur Prüfung öffnen' })).toHaveAttribute('href', '/api/intake-batches/originals/original/content');
  expect(screen.getByRole('button', { name: 'Aufteilung speichern' })).toBeDisabled();
  complete(); fireEvent.click(screen.getByLabelText('Seiten 1–3 in Aufteilung aufnehmen'));
  expect(screen.getByRole('button', { name: 'Aufteilung speichern' })).toBeDisabled();
  fireEvent.click(screen.getByLabelText('Seiten 1–3 in Aufteilung aufnehmen'));
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung speichern' }));
  await waitFor(() => expect(request).toBeDefined());
  expect(JSON.parse(String(request?.body))).toEqual({ logical_document_ids: ['first', 'rest'], reason: 'Zwei getrennte Rechnungen gesichtet', visual_pages_reviewed: true });
  expect(new Headers(request?.headers).get('If-Match')).toBe('"none"');
});

it('retains an uncertain command across refresh and replays the same key and body', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json({}, 503); }
    return respond(url);
  }));
  const view = page(); await screen.findByText('Noch keine geprüfte Aufteilung.'); complete();
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  view.rerender(<SourcePartitionPanel refId="ref" documents={[whole, first, rest]} parentBusy={false} parentGeneration={1} onDenied={vi.fn()} onCreated={vi.fn()} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Aufteilung erneut senden' })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Aufteilung erneut senden' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
});

it('preserves edits on conflict until the user adopts the newest revision', async () => {
  let advanced = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { advanced = true; return json({}, 409); }
    return advanced && url.endsWith('/partition') ? json({ current: revision, can_review: true, can_export: true }) : respond(url);
  }));
  page(); await screen.findByText('Noch keine geprüfte Aufteilung.'); complete();
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung speichern' })); await screen.findByText('Geprüfte Aufteilung · Version 1');
  expect(screen.getByLabelText('Begründung der Aufteilung')).toHaveValue('Zwei getrennte Rechnungen gesichtet');
  expect(screen.getByRole('button', { name: 'Aufteilung speichern' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Neueste Aufteilung übernehmen' }));
  expect(screen.getByLabelText('Seiten 1–3 in Aufteilung aufnehmen')).toBeChecked();
  expect(screen.getByLabelText('Begründung der Aufteilung')).toHaveValue('');
});

it('clears review evidence on denial and stops acting after unmount', async () => {
  let finish: ((value: Response) => void) | undefined;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? await new Promise<Response>(resolve => { finish = resolve; }) : respond(url)));
  const denied = vi.fn(); const view = page(denied); await screen.findByText('Noch keine geprüfte Aufteilung.'); complete();
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung speichern' })); await waitFor(() => expect(finish).toBeDefined());
  view.unmount(); finish!(json({}, 404)); await Promise.resolve(); await Promise.resolve(); expect(denied).not.toHaveBeenCalled();
});

it('creates a bounded page candidate and preserves the exact uncertain range request', async () => {
  const requests: RequestInit[] = []; const created = vi.fn(); const denied = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/logical-documents' && init?.method === 'POST') { requests.push(init); return requests.length === 1 ? json({}, 503) : json({ ...whole, page_end: 2 }, 201); }
    return respond(url);
  }));
  const view = render(<SourcePartitionPanel refId="ref" documents={[whole, first, rest]} parentBusy={false} parentGeneration={0} onDenied={denied} onCreated={created} />);
  await screen.findByText('Noch keine geprüfte Aufteilung.');
  fireEvent.change(screen.getByLabelText('Erste Seite'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('Letzte Seite'), { target: { value: '4' } });
  fireEvent.change(screen.getByLabelText('Grund für den Seitenbereich'), { target: { value: 'Invoice plus annex' } });
  expect(screen.getByRole('button', { name: 'Seitenbereich anlegen', hidden: true })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Letzte Seite'), { target: { value: '2' } });
  fireEvent.click(screen.getByRole('button', { name: 'Seitenbereich anlegen', hidden: true }));
  await screen.findByText(/Wiederholen Sie denselben Seitenbereich/);
  view.rerender(<SourcePartitionPanel refId="ref" documents={[whole, first, rest]} parentBusy={false} parentGeneration={1} onDenied={denied} onCreated={created} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Denselben Seitenbereich erneut senden', hidden: true })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Seitenbereich erneut senden', hidden: true }));
  await waitFor(() => expect(created).toHaveBeenCalledOnce());
  expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
});

it('hides source evidence and editing after a failed fresh partition read', async () => {
  let offline = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/partition') ? (offline ? json({}, 503) : json({ current: revision, can_review: true, can_export: true })) : respond(url)));
  page(); await screen.findByText('Prior review'); offline = true;
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung aktualisieren' }));
  await screen.findByText(/Aufteilung ist derzeit nicht verfügbar/);
  expect(screen.queryByText('Prior review')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Aufteilung speichern' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Original zur Prüfung öffnen' })).not.toBeInTheDocument();
});

it('notifies the parent and clears data after review permission is revoked', async () => {
  const denied = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST' ? json({}, 404) : respond(url)));
  page(denied); await screen.findByText('Noch keine geprüfte Aufteilung.'); complete();
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung speichern' }));
  await waitFor(() => expect(denied).toHaveBeenCalledOnce());
  expect(screen.queryByRole('button', { name: 'Aufteilung speichern' })).not.toBeInTheDocument();
});


it('retains an uncertain range across a failed partition read and recovery', async () => {
  const requests: RequestInit[] = []; let offline = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { requests.push(init); return json({}, 503); }
    return offline && url.endsWith('/partition') ? json({}, 503) : respond(url);
  }));
  page(); await screen.findByText('Noch keine geprüfte Aufteilung.');
  fireEvent.change(screen.getByLabelText('Erste Seite'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('Letzte Seite'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('Grund für den Seitenbereich'), { target: { value: 'Boundary reviewed' } });
  fireEvent.click(screen.getByRole('button', { name: 'Seitenbereich anlegen', hidden: true }));
  await screen.findByText(/Wiederholen Sie denselben Seitenbereich/); offline = true;
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung aktualisieren' }));
  await screen.findByText(/Aufteilung ist derzeit nicht verfügbar/); offline = false;
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung aktualisieren' }));
  await screen.findByText('Noch keine geprüfte Aufteilung.');
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Seitenbereich erneut senden', hidden: true }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
});

it('does not offer original opening without export permission', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/partition') ? json({ current: null, can_review: true, can_export: false }) : respond(url)));
  page(); await screen.findByText('Für das Öffnen des Originals fehlt die Exportberechtigung.');
  expect(screen.queryByRole('link', { name: 'Original zur Prüfung öffnen' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Begründung der Aufteilung')).not.toBeDisabled();
});
