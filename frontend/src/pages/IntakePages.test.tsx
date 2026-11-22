import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { InboxPage } from './InboxPage';
import { IntakeBatchPage } from './IntakeBatchPage';
import { formatUploadLimit } from '../lib/uploads';

vi.mock('../auth/useAuth', () => ({ useAuth: () => ({ session: { role: 'CLERK' } }) }));
vi.mock('../api/intake', async importOriginal => {
  const original = await importOriginal<typeof import('../api/intake')>();
  return { ...original, fileDigest: async () => 'a'.repeat(64) };
});
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
const header = { id: 'batch-1', created_at: '2026-10-03T12:00:00Z', scope_hint_id: null, item_count: 1 };
const item = { id: 'item-1', position: 1, client_item_key: 'key-1', filename: 'invoice.pdf', mime_type: 'application/pdf', content_sha256: 'a'.repeat(64), content_byte_size: 3,
  status: 'failed', original_id: 'original-1', job_id: 'job-1', fence: 'fence-1', error_code: 'scanner_unavailable', can_upload: false, can_retry: true, can_export: false };
const state = { ...header, items: [item], counts: { waiting: 0, received: 0, processing: 0, review: 0, confirmed: 0, failed: 1 }, revision: 'revision-1', next_cursor: null };
function page(path = '/posteingang') {
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/posteingang" element={<InboxPage />} /><Route path="/posteingang/:batchId" element={<IntakeBatchPage />} /></Routes></MemoryRouter>);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('rejects files above the metadata cap before creating an intake batch', async () => {
  const metadata = { max_upload_bytes: 1_294_537 };
  const formattedLimit = formatUploadLimit(metadata.max_upload_bytes);
  const fetch = vi.fn(async (url: string) => url === '/api/meta'
    ? json(metadata) : json({ items: [], next_cursor: null }));
  vi.stubGlobal('fetch', fetch);
  page();
  await waitFor(() => expect(fetch).toHaveBeenCalledWith('/api/meta', expect.anything()));
  expect(await screen.findByText(`Bis zu 100 Dateien, jeweils höchstens ${formattedLimit}. Jede Datei wird einzeln übertragen und vor dem Originalabruf geprüft.`)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Dateien auswählen'), { target: { files: [
    Object.defineProperty(new File(['%PDF-'], 'gross.pdf', { type: 'application/pdf' }), 'size', { value: metadata.max_upload_bytes + 1 }),
  ] } });
  fireEvent.click(screen.getByRole('button', { name: 'Stapel einreichen' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(`höchstens ${formattedLimit}`);
  expect(fetch.mock.calls.every(call => (call as unknown[])[1] === undefined || ((call as unknown[])[1] as RequestInit).method !== 'POST')).toBe(true);
});

it('replays uncertain batch creation with its exact key before separate file requests', async () => {
  const creations: RequestInit[] = []; const uploads: FormData[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/intake-batches' && init?.method === 'POST') {
      creations.push(init); return creations.length === 1 ? json({ detail: 'lost response' }, 503) : json(header, 201);
    }
    if (url.endsWith('/items')) { uploads.push(init!.body as FormData); return json({ status: 'received' }, 202); }
    if (url.includes('/batch-1?')) return json(state);
    return json({ items: [], next_cursor: null });
  }));
  page();
  fireEvent.change(screen.getByLabelText('Dateien auswählen'), { target: { files: [new File(['one'], 'one.pdf', { type: 'application/pdf' }), new File(['two'], 'two.pdf', { type: 'application/pdf' })] } });
  fireEvent.click(screen.getByRole('button', { name: 'Stapel einreichen' }));
  await screen.findByText(/Keine bestätigte Antwort erhalten/);
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Auftrag wiederholen' }));
  await screen.findByRole('heading', { name: 'Dokumentenstapel' });
  expect(creations).toHaveLength(2);
  expect(creations[0].body).toBe(creations[1].body);
  expect(new Headers(creations[0].headers).get('Idempotency-Key')).toBe(new Headers(creations[1].headers).get('Idempotency-Key'));
  expect(uploads).toHaveLength(2);
  expect(uploads.every(form => form.getAll('file').length === 1)).toBe(true);
  expect(uploads[0].get('client_item_key')).not.toBe(uploads[1].get('client_item_key'));
});

it('rejects an oversized manifest before creating a batch', async () => {
  const fetch = vi.fn(async () => json({ items: [], next_cursor: null })); vi.stubGlobal('fetch', fetch);
  page();
  fireEvent.change(screen.getByLabelText('Dateien auswählen'), { target: { files: Array.from({ length: 101 }, () => new File(['x'], 'x.pdf', { type: 'application/pdf' })) } });
  fireEvent.click(screen.getByRole('button', { name: 'Stapel einreichen' }));
  await screen.findByRole('alert');
  expect(fetch.mock.calls.every(call => (call as unknown[])[1] === undefined || ((call as unknown[])[1] as RequestInit).method !== 'POST')).toBe(true);
});

it('reconnects to durable failed status without offering a quarantined download', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json(state)));
  page('/posteingang/batch-1');
  await screen.findByText(/Dateiprüfung ist derzeit nicht verfügbar/);
  expect(screen.queryByRole('link', { name: 'Original herunterladen' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Prüfung erneut anfordern' })).toBeDisabled();
  expect(screen.getByText(/bestätigt keine Kosten/)).toBeInTheDocument();
});

it('preserves retry reason and key across refresh and uncertain response', async () => {
  const retries: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/retry')) { retries.push(init!); return json({ detail: 'uncertain' }, 503); }
    return json(state);
  }));
  page('/posteingang/batch-1');
  const reason = await screen.findByLabelText('Grund für die Wiederholung');
  fireEvent.change(reason, { target: { value: 'Scanner ist wieder verfügbar' } });
  fireEvent.click(screen.getByRole('button', { name: 'Stand aktualisieren' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Prüfung erneut anfordern' })).not.toBeDisabled());
  expect(screen.getByLabelText('Grund für die Wiederholung')).toHaveValue('Scanner ist wieder verfügbar');
  fireEvent.click(screen.getByRole('button', { name: 'Prüfung erneut anfordern' }));
  await screen.findByText(/Keine bestätigte Antwort erhalten/);
  fireEvent.click(screen.getByRole('button', { name: 'Denselben Prüfauftrag wiederholen' }));
  await waitFor(() => expect(retries).toHaveLength(2));
  expect(retries[0].body).toBe(retries[1].body);
  expect(new Headers(retries[0].headers).get('Idempotency-Key')).toBe(new Headers(retries[1].headers).get('Idempotency-Key'));
});

it('removes cached source actions after authorization is lost', async () => {
  let revoked = false;
  vi.stubGlobal('fetch', vi.fn(async () => revoked ? json({ detail: 'revoked' }, 404) : json({ ...state, items: [{ ...item, status: 'review', can_retry: false, can_export: true }] })));
  page('/posteingang/batch-1');
  await screen.findByRole('link', { name: 'Original herunterladen' }); revoked = true;
  fireEvent.click(screen.getByRole('button', { name: 'Stand aktualisieren' }));
  await screen.findByRole('alert');
  expect(screen.queryByRole('link', { name: 'Original herunterladen' })).not.toBeInTheDocument();
  expect(screen.queryByText('invoice.pdf')).not.toBeInTheDocument();
});


it('suppresses cached download links when fresh status is unavailable', async () => {
  let offline = false;
  vi.stubGlobal('fetch', vi.fn(async () => offline ? json({ detail: 'unavailable' }, 503) : json({ ...state, items: [{ ...item, status: 'review', can_retry: false, can_export: true }] })));
  page('/posteingang/batch-1');
  await screen.findByRole('link', { name: 'Original herunterladen' }); offline = true;
  fireEvent.click(screen.getByRole('button', { name: 'Stand aktualisieren' }));
  await screen.findByRole('alert');
  expect(screen.getByText('invoice.pdf')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Original herunterladen' })).not.toBeInTheDocument();
});

// ---- how an extraction that ended is explained on the batch page (issue #501) -------------------------------------------

const extracting = (code: string, over = {}) => ({ ...item, stage: 'extraction', document_ref_id: 'ref-1', logical_counts: { total: 1, processing: 0, review: 0, failed: 1 }, error_code: code, can_retry: false, ...over });

it.each([
  ['ai_import_disabled', /Die automatische Auswertung per KI ist für Ihre Organisation nicht eingerichtet.*Es wird nichts \(mehr\) an einen KI-Anbieter übermittelt/s],
  ['organization_ai_budget_exceeded', /monatliche KI-Budget Ihrer Organisation ist ausgeschöpft.*nicht gesendet/s],
  ['model_reading_failed', /Das Modell unter „Dokumente einlesen“ hat eine Seite nicht eingelesen.*Das Original bleibt erhalten; es wurde nichts übernommen/s],
  ['render_unavailable', /Eine Seite des Belegs konnte nicht zum Einlesen aufbereitet werden/],
  ['processor_failed', /Die Auswertung ist fehlgeschlagen\. Das Original bleibt erhalten/],
  ['attempt_limit', /dreimal versucht und nicht abgeschlossen/],
])('explains the extraction code %s in German from the one map of reasons', async (code, expected) => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...state, items: [extracting(code)] }))); page('/posteingang/batch-1');
  expect(await screen.findByText(expected)).toBeInTheDocument();
  expect(screen.getByText(/^Auswertung · 1 Beleg\(e\)/)).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent(new RegExp(code));
});

it('names no text recognition any more and keeps the explanation of a result that an earlier release stored', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...state, items: [extracting('ocr_unavailable')] }))); page('/posteingang/batch-1');
  expect(await screen.findByText(/Die frühere Texterkennung konnte diesen Beleg nicht auswerten\. Das Original bleibt erhalten/)).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent(/derzeit nicht verfügbar|lokale Texterkennung/);
});

it('keeps the explanations of the inspection of a file, and a general sentence for a code nothing explains', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...state, items: [{ ...extracting('scanner_detected'), stage: 'inspection' }, { ...extracting('a_code_nobody_mapped'), id: 'item-2', stage: 'extraction' }] })));
  page('/posteingang/batch-1');
  expect(await screen.findByText('Die Sicherheitsprüfung hat die Datei zurückgewiesen. Das Original bleibt gesperrt.')).toBeInTheDocument();
  expect(screen.getByText(/Die Auswertung konnte nicht abgeschlossen werden\. Das Original bleibt erhalten/)).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent(/a_code_nobody_mapped/);
});

// A document that waits for the organization is not counted as blocked or failed (review of PR #832).
const tile = (label: string) => screen.getAllByText(label).find(element => element.tagName === 'P')!.previousElementSibling!;

it('counts a document that waits for the organizations setup as received and keeps the other failures as failed', async () => {
  const waiting = ['ai_import_disabled', 'ai_inbox_confirmation_missing', 'ai_document_model_missing', 'ai_key_unreadable'].map((code, index) => extracting(code, { id: `waiting-${index}`, can_retry: true }));
  const failed = extracting('model_extraction_failed', { id: 'failed-1' });
  const blocked = { ...item, id: 'blocked-1', stage: 'inspection', error_code: 'scanner_detected', can_retry: false };
  const received = { ...item, id: 'received-1', status: 'received', error_code: null, can_retry: false };
  const counts = { waiting: 0, received: 1, processing: 0, review: 0, confirmed: 0, failed: 6 };
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...state, item_count: 7, counts, items: [received, ...waiting, failed, blocked] })));
  page('/posteingang/batch-1');
  await screen.findByRole('heading', { name: 'Dokumentenstapel' });
  expect(tile('Empfangen')).toHaveTextContent('5');
  expect(tile('Gesperrt / fehlgeschlagen')).toHaveTextContent('2');
  expect(screen.getAllByText('Empfangen').filter(element => element.className.includes('badge'))).toHaveLength(5);
  expect(screen.getAllByText('Gesperrt / fehlgeschlagen').filter(element => element.className.includes('badge'))).toHaveLength(2);
});

it('does not poll for a document that waits for the setup of the organization', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const fetch = vi.fn(async () => json({ ...state, counts: { waiting: 0, received: 0, processing: 0, review: 0, confirmed: 0, failed: 1 }, items: [extracting('ai_import_disabled', { status: 'failed', can_retry: true })] }));
  vi.stubGlobal('fetch', fetch);
  try {
    page('/posteingang/batch-1');
    await screen.findByRole('heading', { name: 'Dokumentenstapel' });
    expect(tile('Empfangen')).toHaveTextContent('1');
    const loads = fetch.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetch.mock.calls.length).toBe(loads);
  } finally { vi.useRealTimers(); }
});

// A multipage upload waits for the division (owner decision of 2026-11-22 on #501): it is the reviewer's turn, nothing failed.
it('says that a multipage upload waits for the division, that nothing was sent and where to act, without a failure count', async () => {
  const waiting = { ...item, status: 'review', stage: 'split', document_ref_id: 'ref-1', logical_counts: { total: 3, processing: 0, review: 0, failed: 0 },
    error_code: 'awaiting_split', can_retry: false };
  const counts = { waiting: 0, received: 0, processing: 0, review: 1, confirmed: 0, failed: 0 };
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...state, counts, items: [waiting] }))); page('/posteingang/batch-1');
  expect(await screen.findByText(/^Das Original hat mehr als eine Seite und wird erst ausgewertet, wenn Sie bestätigt haben, wie es aufgeteilt ist/)).toBeInTheDocument();
  expect(screen.getByText(/Bis dahin wurde nichts an einen KI-Anbieter übermittelt/)).toBeInTheDocument();
  expect(screen.getByText('Aufteilung ausstehend · 3 Beleg(e), 0 zur Sichtung')).toBeInTheDocument();
  expect(tile('Zur Sichtung')).toHaveTextContent('1');
  expect(tile('Gesperrt / fehlgeschlagen')).toHaveTextContent('0');
  expect(screen.getByRole('link', { name: 'Belege und Vorschläge ansehen' })).toHaveAttribute('href', '/posteingang/belege/ref-1');
  expect(screen.queryByText(/Grund für die Wiederholung/)).not.toBeInTheDocument();
  expect(screen.getByText(/ein Original mit mehreren Seiten erst, wenn Sie unter „Belege und Vorschläge“ bestätigt haben, wie es aufgeteilt ist/)).toBeInTheDocument();
});
