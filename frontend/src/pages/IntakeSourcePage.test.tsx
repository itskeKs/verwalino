import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { IntakeSourcePage } from './IntakeSourcePage';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const child = { id: 'logical-1', document_ref_id: 'ref-1', page_start: 2, page_end: 2, verified_page_count: 3, origin: 'manual', review_state: 'unreviewed', reason: 'Candidate' };
const status = { id: 'job-1', status: 'failed', attempt_number: 1, fence: 'fence-1', error_code: 'processor_failed', can_retry: true };
const revision = { id: 'revision-1', logical_document_id: 'logical-1', status: 'needs_review', error_code: 'missing_fields', created_at: '2026-10-03T12:00:00Z',
  fields: [{ field_name: 'gross_amount', value: '119.00', confidence: '0.85', locator_status: 'page_quote', page_number: 2, quotation: 'Gesamtbetrag: 119,00 EUR', xml_path: null, method: 'text_layer' },
    { field_name: 'document_number', value: 'R-1234', confidence: null, locator_status: 'unlocated', page_number: null, quotation: null, xml_path: null, method: 'unlocated' }] };
function response(url: string) {
  if (url.endsWith('/partition')) return json({ current: null, can_review: true, can_export: true });
  if (url === '/api/document-refs/ref-1') return json({ source_kind: 'intake_original', source_id: 'original-1' });
  if (url.startsWith('/api/logical-documents?')) return json({ items: [child] });
  if (url.endsWith('/processing')) return json(status);
  if (url.startsWith('/api/document-refs/')) return json({ items: [{ id: revision.id }] });
  return json(revision);
}
function page() { render(<MemoryRouter initialEntries={['/posteingang/belege/ref-1']}><Routes><Route path="/posteingang/belege/:documentRefId" element={<IntakeSourcePage />} /></Routes></MemoryRouter>); }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('renders unverified source evidence and requests complete child history', async () => {
  const fetch = vi.fn(async (url: string) => response(url)); vi.stubGlobal('fetch', fetch); page();
  await screen.findByText('Gesamtbetrag: 119,00 EUR');
  expect(screen.getByText('Fundstelle auf Seite 2 · PDF-Text')).toBeInTheDocument();
  expect(screen.getByText('Keine eindeutige Fundstelle ermittelt.')).toBeInTheDocument();
  expect(screen.getByText(/bestätigen keine Kosten/)).toBeInTheDocument();
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/extractions?logical_document_id=logical-1'))).toBe(true);
});

it('clears quotes and source selection after access revocation', async () => {
  let revoked = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => revoked && url.endsWith('/processing') ? json({ detail: 'revoked' }, 404) : response(url)));
  page(); await screen.findByText('Gesamtbetrag: 119,00 EUR'); revoked = true;
  fireEvent.click(screen.getByRole('button', { name: 'Vorschläge aktualisieren' }));
  await screen.findByText(/Original ist nicht mehr zugänglich/);
  expect(screen.queryByText('Gesamtbetrag: 119,00 EUR')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Beleg auswählen')).not.toBeInTheDocument();
});

it('replays uncertain retry unchanged across parent refresh', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/retry-extraction')) { requests.push(init!); return json({ detail: 'uncertain' }, 503); }
    return response(url);
  }));
  page(); const reason = await screen.findByLabelText('Grund für die erneute Texterkennung');
  fireEvent.change(reason, { target: { value: 'Lokale Texterkennung wieder verfügbar' } });
  fireEvent.click(screen.getByRole('button', { name: 'Texterkennung erneut anfordern' }));
  await screen.findByText(/Keine bestätigte Antwort erhalten/);
  fireEvent.click(screen.getByRole('button', { name: 'Belege aktualisieren' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Denselben Auftrag wiederholen' })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Auftrag wiederholen' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
});

it('hides retry and field data after a failed fresh read', async () => {
  let offline = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => offline && url.endsWith('/processing') ? json({ detail: 'offline' }, 503) : response(url)));
  page(); await screen.findByText('Gesamtbetrag: 119,00 EUR'); offline = true;
  fireEvent.click(screen.getByRole('button', { name: 'Vorschläge aktualisieren' }));
  await screen.findByRole('alert');
  expect(screen.queryByText('Gesamtbetrag: 119,00 EUR')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Texterkennung erneut anfordern' })).not.toBeInTheDocument();
});

it('allows a new retry reason when a later attempt fails', async () => {
  let advanced = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/retry-extraction')) { advanced = true; return json({}, 202); }
    if (url.endsWith('/processing')) return json(advanced ? { ...status, fence: 'fence-2', attempt_number: 2 } : status);
    return response(url);
  }));
  page(); fireEvent.change(await screen.findByLabelText('Grund für die erneute Texterkennung'), { target: { value: 'First recovery' } });
  fireEvent.click(screen.getByRole('button', { name: 'Texterkennung erneut anfordern' }));
  await screen.findByText(/Versuch 2 von höchstens 3/);
  expect(screen.getByLabelText('Grund für die erneute Texterkennung')).not.toBeDisabled();
  expect(screen.getByLabelText('Grund für die erneute Texterkennung')).toHaveValue('');
});

it('ignores an old retry denial after switching to another logical source', async () => {
  let finish: ((response: Response) => void) | undefined;
  const second = { ...child, id: 'logical-2', page_start: 3, page_end: 3 };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/logical-documents?')) return json({ items: [child, second] });
    if (url.endsWith('/retry-extraction')) return await new Promise<Response>(resolve => { finish = resolve; });
    if (url.includes('logical-2/processing')) return json({ ...status, can_retry: false, status: 'succeeded' });
    if (url.includes('logical_document_id=logical-2')) return json({ items: [{ id: 'revision-2' }] });
    if (url.endsWith('/revision-2')) return json({ ...revision, id: 'revision-2', fields: [{ ...revision.fields[0], value: '238.00', quotation: 'Gesamtbetrag: 238,00 EUR', page_number: 3 }] });
    return response(url);
  }));
  page(); fireEvent.change(await screen.findByLabelText('Grund für die erneute Texterkennung'), { target: { value: 'Retry first source' } });
  fireEvent.click(screen.getByRole('button', { name: 'Texterkennung erneut anfordern' }));
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.change(screen.getByLabelText('Beleg auswählen'), { target: { value: 'logical-2' } });
  await screen.findByText('Gesamtbetrag: 238,00 EUR');
  finish!(json({ detail: 'Late denial for previous source' }, 404));
  await waitFor(() => expect(screen.getByText('Gesamtbetrag: 238,00 EUR')).toBeInTheDocument());
  expect(screen.getByLabelText('Beleg auswählen')).toHaveValue('logical-2');
});

it('preserves an uncertain partition through a transient parent read failure', async () => {
  const whole = { ...child, page_start: 1, page_end: 3 }; const requests: RequestInit[] = []; let offline = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/partition') && init?.method === 'POST') { requests.push(init); return json({}, 503); }
    if (url.startsWith('/api/logical-documents?')) return offline ? json({}, 503) : json({ items: [whole] });
    return response(url);
  }));
  page(); await screen.findByText('Noch keine geprüfte Aufteilung.');
  fireEvent.click(screen.getByLabelText('Seiten 1–3 in Aufteilung aufnehmen'));
  fireEvent.change(screen.getByLabelText('Begründung der Aufteilung'), { target: { value: 'Whole document reviewed' } });
  fireEvent.click(screen.getByLabelText(/Alle sichtbaren Seiten im Original geprüft/));
  fireEvent.click(screen.getByRole('button', { name: 'Aufteilung speichern' })); await screen.findByText(/Keine bestätigte Antwort erhalten. Senden/);
  offline = true; fireEvent.click(screen.getByRole('button', { name: 'Belege aktualisieren' }));
  await screen.findByText('Belege nicht verfügbar. Bitte Berechtigung und Verbindung prüfen.');
  expect(screen.queryByRole('button', { name: 'Dieselbe Aufteilung erneut senden' })).not.toBeInTheDocument();
  offline = false; fireEvent.click(screen.getByRole('button', { name: 'Belege aktualisieren' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Aufteilung erneut senden' })).not.toBeDisabled());
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Aufteilung erneut senden' }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  expect(new Headers(requests[0].headers).get('Idempotency-Key')).toBe(new Headers(requests[1].headers).get('Idempotency-Key'));
});
