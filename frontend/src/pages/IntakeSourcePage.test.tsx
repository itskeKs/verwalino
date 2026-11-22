import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { IntakeSourcePage } from './IntakeSourcePage';

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const child = { id: 'logical-1', document_ref_id: 'ref-1', page_start: 2, page_end: 2, verified_page_count: 3, origin: 'manual', review_state: 'unreviewed', reason: 'Candidate' };
const status = { id: 'job-1', status: 'failed', attempt_number: 1, fence: 'fence-1', error_code: 'processor_failed', can_retry: true };
const revision = { id: 'revision-1', logical_document_id: 'logical-1', status: 'needs_review', error_code: 'missing_fields', created_at: '2026-10-03T12:00:00Z',
  payload: { gross_amount: '119.00' }, fields: [{ field_name: 'gross_amount', value: '119.00', confidence: '0.85', locator_status: 'page_quote', page_number: 2, quotation: 'Gesamtbetrag: 119,00 EUR', xml_path: null, method: 'text_layer' },
    { field_name: 'document_number', value: 'R-1234', confidence: null, locator_status: 'unlocated', page_number: null, quotation: null, xml_path: null, method: 'unlocated' }] };
function response(url: string) {
  if (url.endsWith('/model-usage')) return json({ stages: [], total: null });
  if (url.includes('/allocation-adoptions')) return json({ item: null });
  if (url.includes('/allocation-treatments')) return json({ items: [], next_before_revision: null, reviewable_allocation_id: null });
  if (url.includes('/assignment-candidates')) return json({ items: [], unmatched_evidence: [], status: 'no_labelled_evidence', can_prepare: false });
  if (url.includes('/duplicate-candidates')) return json({ items: [], next_cursor: null, financial_state: 'evidence_only' });
  if (url.includes('/allocation-proposals')) return json({ items: [], next_before_revision: null });
  if (url === '/api/cost-categories') return json([]);
  if (url.includes('/context-decisions')) return json({ items: [], next_before_revision: null });
  if (url.includes('/invoice-reviews')) return json({ items: [], next_before_revision: null, reviewable_assignment_id: null });
  if (url.includes('/assignment-proposals')) return json({ items: [], next_before_revision: null });
  if (url.startsWith('/api/accounting-scopes/')) return json({ items: [], next_cursor: null });
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

// The values of a proposal are shown in the project's display formats, as the invoice page shows them (review of PR #832).
const stored = (field_name: string, value: string) => ({ field_name, value, confidence: '0.9', locator_status: 'unlocated', page_number: null, quotation: null, xml_path: null, method: 'unlocated' });
const category = { id: 'category-1', code: 'GEBAEUDEVERSICHERUNG', label: 'Gebäudeversicherung', plan_version: 1 };

function withFields(fields: ReturnType<typeof stored>[], categories: unknown[] = [category]) {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url === '/api/cost-categories' ? json(categories)
    : url.startsWith('/api/extraction-revisions/') ? json({ ...revision, status: 'succeeded', error_code: null, processor_version: 'intake-model-v1', fields }) : response(url)));
  page();
}
const card = (label: string) => screen.getByRole('heading', { name: label }).closest('article')!;

it('shows dates, amounts, the VAT rate and the category in the display formats', async () => {
  withFields([stored('invoice_date', '2026-02-03'), stored('gross_amount', '1234.50'), stored('vat_amount', '197.12'), stored('vat_rate', '19.00'),
    stored('service_period_from', '2026-01-01'), stored('service_period_to', '2026-12-31'), stored('suggested_category_code', 'GEBAEUDEVERSICHERUNG'),
    stored('vendor_name', 'Mustermann Versicherungen AG'), stored('document_number', 'RE-2026-0042')]);
  await screen.findByRole('heading', { name: 'Rechnungsdatum' });
  await waitFor(() => expect(card('Vorgeschlagene Kostenart')).toHaveTextContent('Gebäudeversicherung'));
  expect(card('Rechnungsdatum')).toHaveTextContent('03.02.2026');
  expect(card('Bruttobetrag')).toHaveTextContent('1.234,50 €');
  expect(card('Umsatzsteuerbetrag')).toHaveTextContent('197,12 €');
  expect(card('Umsatzsteuersatz')).toHaveTextContent('19 %');
  expect(card('Leistungszeitraum von')).toHaveTextContent('01.01.2026');
  expect(card('Leistungszeitraum bis')).toHaveTextContent('31.12.2026');
  expect(card('Aussteller')).toHaveTextContent('Mustermann Versicherungen AG');
  expect(card('Belegnummer')).toHaveTextContent('RE-2026-0042');
  for (const stale of ['2026-02-03', '1234.50', '19.00', 'GEBAEUDEVERSICHERUNG']) expect(document.body).not.toHaveTextContent(stale);
});

it('shows a value that a format cannot read, and a category the list does not name, as stored', async () => {
  withFields([stored('gross_amount', 'ca. 312,00'), stored('invoice_date', 'Anfang Februar'), stored('suggested_category_code', 'UNBEKANNT')], []);
  await screen.findByRole('heading', { name: 'Bruttobetrag' });
  expect(card('Bruttobetrag')).toHaveTextContent('ca. 312,00');
  expect(card('Rechnungsdatum')).toHaveTextContent('Anfang Februar');
  expect(card('Vorgeschlagene Kostenart')).toHaveTextContent('UNBEKANNT');
});

it('shows the stored category code while the list of categories is not available', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url === '/api/cost-categories' ? json({ detail: 'down' }, 503)
    : url.startsWith('/api/extraction-revisions/') ? json({ ...revision, fields: [stored('suggested_category_code', 'GEBAEUDEVERSICHERUNG')] }) : response(url)));
  page();
  await screen.findByRole('heading', { name: 'Vorgeschlagene Kostenart' });
  expect(card('Vorgeschlagene Kostenart')).toHaveTextContent('GEBAEUDEVERSICHERUNG');
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
  page(); const reason = await screen.findByLabelText('Grund für die erneute Auswertung');
  fireEvent.change(reason, { target: { value: 'Modelle wieder eingerichtet' } });
  fireEvent.click(screen.getByRole('button', { name: 'Auswertung erneut anfordern' }));
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
  expect(screen.queryByRole('button', { name: 'Auswertung erneut anfordern' })).not.toBeInTheDocument();
});

it('counts only the attempts that made a request: none is named before the first one', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/processing')
    ? json({ ...status, status: 'unavailable', error_code: 'ai_import_disabled', attempt_number: 0 }) : response(url)));
  page();
  expect(await screen.findByText('Auswertung nicht möglich')).toBeInTheDocument();
  expect(screen.queryByText(/Versuch \d/)).not.toBeInTheDocument();
});

it('allows a new retry reason when a later attempt fails', async () => {
  let advanced = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/retry-extraction')) { advanced = true; return json({}, 202); }
    if (url.endsWith('/processing')) return json(advanced ? { ...status, fence: 'fence-2', attempt_number: 2 } : status);
    return response(url);
  }));
  page(); fireEvent.change(await screen.findByLabelText('Grund für die erneute Auswertung'), { target: { value: 'First recovery' } });
  fireEvent.click(screen.getByRole('button', { name: 'Auswertung erneut anfordern' }));
  await screen.findByText(/Versuch 2 von höchstens 3/);
  expect(screen.getByLabelText('Grund für die erneute Auswertung')).not.toBeDisabled();
  expect(screen.getByLabelText('Grund für die erneute Auswertung')).toHaveValue('');
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
  page(); fireEvent.change(await screen.findByLabelText('Grund für die erneute Auswertung'), { target: { value: 'Retry first source' } });
  fireEvent.click(screen.getByRole('button', { name: 'Auswertung erneut anfordern' }));
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


it('does not offer assignment for a needs-review result without usable payload', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.endsWith('/partition')) return json({ current: { id: 'partition', logical_document_ids: [child.id] }, can_review: true, can_export: false });
    if (url.endsWith('/revision-1')) return json({ ...revision, payload: null, fields: [], error_code: 'incomplete_text' });
    return response(url);
  }));
  page(); await screen.findByText('Für einen neuen Vorschlag wird ein verfügbarer Extraktionsstand benötigt.');
  expect(await screen.findByRole('button', { name: 'Zuordnung vorschlagen' })).toBeDisabled();
});

it('selects the linked non-first child and pins an older extraction outside the loaded history', async () => {
  const second = { ...child, id: 'logical-2', page_start: 3, page_end: 3 };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url.startsWith('/api/logical-documents?')) return json({ items: [child, second] });
    if (url.includes('logical_document_id=logical-2')) return json({ items: [{ id: 'latest-2' }] });
    if (url.endsWith('/latest-2')) return json({ ...revision, id: 'latest-2', document_ref_id: 'ref-1', logical_document_id: second.id, fields: [] });
    if (url.endsWith('/pinned-2')) return json({ ...revision, id: 'pinned-2', document_ref_id: 'ref-1', logical_document_id: second.id, fields: [{ ...revision.fields[0], quotation: 'Historischer Betrag des zweiten Belegs' }] });
    return response(url);
  }));
  render(<MemoryRouter initialEntries={['/posteingang/belege/ref-1?logical_document_id=logical-2&extraction_revision_id=pinned-2']}><Routes><Route path="/posteingang/belege/:documentRefId" element={<IntakeSourcePage />} /></Routes></MemoryRouter>);
  await screen.findByText('Historischer Betrag des zweiten Belegs');
  expect(screen.getByLabelText('Beleg auswählen')).toHaveValue('logical-2');
  expect(screen.getByText(/Grundlage des Klärungsfalls · Früherer Versuch/).closest('details')).toHaveAttribute('open');
  expect(screen.queryByText('Gesamtbetrag: 119,00 EUR')).not.toBeInTheDocument();
});

it('rejects a linked child from another original without falling back to the first child', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => response(url)));
  render(<MemoryRouter initialEntries={['/posteingang/belege/ref-1?logical_document_id=foreign']}><Routes><Route path="/posteingang/belege/:documentRefId" element={<IntakeSourcePage />} /></Routes></MemoryRouter>);
  await screen.findByText(/Der verknüpfte Teilbeleg gehört nicht/);
  expect(screen.queryByLabelText('Beleg auswählen')).not.toBeInTheDocument();
  expect(screen.queryByText('Gesamtbetrag: 119,00 EUR')).not.toBeInTheDocument();
});

it('rejects a pinned extraction from a different child before displaying its fields', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/foreign-extraction') ? json({ ...revision, id: 'foreign-extraction', document_ref_id: 'ref-1', logical_document_id: 'other', fields: [{ ...revision.fields[0], quotation: 'Wrong source quote' }] }) : response(url)));
  render(<MemoryRouter initialEntries={['/posteingang/belege/ref-1?logical_document_id=logical-1&extraction_revision_id=foreign-extraction']}><Routes><Route path="/posteingang/belege/:documentRefId" element={<IntakeSourcePage />} /></Routes></MemoryRouter>);
  await screen.findByText('Der verknüpfte Extraktionsstand gehört nicht zu diesem Beleg.');
  expect(screen.queryByText('Wrong source quote')).not.toBeInTheDocument();
  expect(screen.queryByText('Gesamtbetrag: 119,00 EUR')).not.toBeInTheDocument();
});

// ---- the outcome of the models, what they cost, and where a value was found (issue #501) ----------------------------

const reportedUsage = { stages: [
  { stage: 'READING', model: 'gpt-5.4-mini', calls: 2, usage_status: 'REPORTED', reported_input_tokens: 2000, reported_output_tokens: 400, input_tokens: 2000, output_tokens: 400, estimated_cost_usd: '0.0033' },
  { stage: 'EXTRACTION', model: 'gpt-5.6-luna', calls: 2, usage_status: 'RESERVED', reported_input_tokens: null, reported_output_tokens: null, input_tokens: 3000, output_tokens: 3000, estimated_cost_usd: '0.0201' }],
  total: { usage_status: 'PARTIAL', reported_input_tokens: 2000, reported_output_tokens: 400, input_tokens: 5000, output_tokens: 3400, estimated_cost_usd: '0.0234' } };

it('explains the state of the document and of each attempt in German from the one map of reasons, never by code', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => response(url))); page();
  // The document ended in a failure; its newest attempt is a proposal that lacks fields.
  expect(await screen.findByText(/Die Auswertung ist fehlgeschlagen\. Das Original bleibt erhalten; es wurde nichts übernommen\./)).toBeInTheDocument();
  expect(screen.getByText(/Die Auswertung hat nicht alle nötigen Angaben gefunden \(mindestens Rechnungsdatum und Bruttobetrag\)/)).toBeInTheDocument();
  expect(document.body).not.toHaveTextContent(/processor_failed|missing_fields|Texterkennung/);
});

it.each([
  ['unavailable', 'ai_inbox_confirmation_missing', /noch nicht bestätigt, dass Dokumente im Posteingang an den KI-Anbieter übermittelt werden dürfen.*Es wird nichts \(mehr\) an einen KI-Anbieter übermittelt.*ohne den Schlüssel erneut einzugeben/s],
  ['unavailable', 'organization_ai_budget_exceeded', /monatliche KI-Budget Ihrer Organisation ist ausgeschöpft.*Verbrauch der KI-Verarbeitung/s],
  ['failed', 'model_extraction_failed', /Das Modell unter „Informationen extrahieren“ hat keine Angaben geliefert.*verursacht erneut Kosten/s],
  ['needs_review', 'invoice_page_limit', /zu viele Seiten.*nicht an einen KI-Anbieter übermittelt.*kleineren Seitenbereich/s],
  ['unavailable', 'ai_settings_changed', /während dieser Beleg ausgelesen wurde/],
])('says what happened and what to do for %s / %s', async (state, code, expected) => {
  const failed = { ...status, status: state, error_code: code, can_retry: state !== 'needs_review' };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/processing') ? json(failed) : url.includes('/revision') || url.includes('/extraction-revisions/') ? json({ ...revision, status: state, error_code: code, payload: null, fields: [] }) : response(url)));
  page();
  expect((await screen.findAllByText(expected)).length).toBeGreaterThan(0);
  expect(document.body).not.toHaveTextContent(new RegExp(code));
  expect(screen.queryByText(/Texterkennung/)).not.toBeInTheDocument();
});

it('shows what the models have cost for the document over all attempts, reservations as such', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/model-usage') ? json(reportedUsage) : response(url))); page();
  expect(await screen.findByRole('heading', { name: 'Verbrauch der KI-Verarbeitung' })).toBeInTheDocument();
  expect(screen.getByRole('group', { name: 'Dokumente einlesen' })).toHaveTextContent('2 Aufrufe');
  expect(screen.getByRole('group', { name: 'Dokumente einlesen' })).toHaveTextContent('0,0033 USD');
  expect(screen.getByRole('group', { name: 'Informationen extrahieren' })).toHaveTextContent('Reservierte Eingabetoken');
  expect(screen.getByRole('group', { name: 'Gesamt' })).toHaveTextContent('0,0234 USD');
  expect(document.body).not.toHaveTextContent(/0[,.]00 USD/);
});

it('shows no usage before a request was made and reads it again when the document changes state', async () => {
  let advanced = false;
  const fetch = vi.fn(async (url: string) => {
    if (url.endsWith('/retry-extraction')) { advanced = true; return json({}, 202); }
    if (url.endsWith('/processing')) return json(advanced ? { ...status, status: 'running', attempt_number: 2 } : status);
    if (url.endsWith('/model-usage')) return json(advanced ? reportedUsage : { stages: [], total: null });
    return response(url);
  });
  vi.stubGlobal('fetch', fetch); page();
  await screen.findByText(/Versuch 1 von höchstens 3/);
  expect(screen.queryByRole('heading', { name: 'Verbrauch der KI-Verarbeitung' })).not.toBeInTheDocument();
  fireEvent.change(await screen.findByLabelText('Grund für die erneute Auswertung'), { target: { value: 'Modelle eingerichtet' } });
  fireEvent.click(screen.getByRole('button', { name: 'Auswertung erneut anfordern' }));
  expect(await screen.findByRole('heading', { name: 'Verbrauch der KI-Verarbeitung' })).toBeInTheDocument();
});

it('says so when the usage cannot be loaded', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/model-usage') ? json({ detail: 'x' }, 500) : response(url))); page();
  expect(await screen.findByText('Der Verbrauch der KI-Verarbeitung konnte nicht geladen werden.')).toBeInTheDocument();
});

const modelRevision = { ...revision, processor_version: 'intake-model-v1', status: 'succeeded', error_code: null, fields: [
  { field_name: 'gross_amount', value: '99.00', confidence: '0.9', locator_status: 'page_quote', page_number: 4, quotation: 'Gesamtbetrag  99,00 EUR\nSeite 4', xml_path: null, method: 'reading_model' },
  { field_name: 'vendor_name', value: 'Mustermann Versicherungen AG', confidence: '0.95', locator_status: 'page_quote', page_number: 3, quotation: 'Mustermann Versicherungen AG', xml_path: null, method: 'text_layer' },
  { field_name: 'document_number', value: 'R-1234', confidence: '0.9', locator_status: 'unlocated', page_number: null, quotation: null, xml_path: null, method: 'unlocated' }] };

it('locates a value the model found on its page, says how the page was read, and never claims a location it could not confirm', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/processing') ? json({ ...status, status: 'succeeded', error_code: null, can_retry: false }) : url.includes('/extraction-revisions/') ? json(modelRevision) : response(url)));
  page();
  expect(await screen.findByText('Fundstelle auf Seite 4 · vom Lesemodell gelesen')).toBeInTheDocument();
  expect(screen.getByText('Fundstelle auf Seite 3 · PDF-Text')).toBeInTheDocument();
  expect(screen.getByText((_, element) => element?.tagName === 'BLOCKQUOTE' && element.textContent === 'Gesamtbetrag  99,00 EUR\nSeite 4')).toBeInTheDocument();
  expect(screen.getByText('Keine Fundstelle bestätigt: Das Modell hat keine Seite und Textstelle genannt, die sich in diesem Beleg nachweisen lässt.')).toBeInTheDocument();
  expect(screen.queryByText('Keine eindeutige Fundstelle ermittelt.')).not.toBeInTheDocument();
  expect(screen.getByText(/Fundstellen helfen bei der Sichtung; sie bestätigen keine Kosten/)).toBeInTheDocument();
});

it('keeps naming text recognition for the pages that an earlier release recognized', async () => {
  const earlier = { ...revision, processor_version: 'intake-local-v1', fields: [{ ...revision.fields[0], method: 'ocr' }, revision.fields[1]] };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.includes('/extraction-revisions/') ? json(earlier) : response(url))); page();
  expect(await screen.findByText('Fundstelle auf Seite 2 · Texterkennung (frühere Version)')).toBeInTheDocument();
  expect(screen.getByText('Keine eindeutige Fundstelle ermittelt.')).toBeInTheDocument();
});

it('says that a document waits for the division, offers no retry and names where to confirm it', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => url.endsWith('/processing')
    ? json({ ...status, status: 'awaiting_split', error_code: 'awaiting_split', can_retry: false, attempt_number: 0 }) : url.includes('/extractions?') ? json({ items: [] }) : response(url))); page();
  // Nothing was requested, so none of the three attempts is used and the line names none.
  expect(await screen.findByText('Wartet auf die Aufteilung')).toBeInTheDocument();
  expect(screen.queryByText(/Versuch \d/)).not.toBeInTheDocument();
  expect(screen.getAllByText(/Das Original hat mehr als eine Seite und wird erst ausgewertet.*Bis dahin wurde nichts an einen KI-Anbieter übermittelt/)).not.toHaveLength(0);
  expect(screen.queryByRole('button', { name: 'Auswertung erneut anfordern' })).not.toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Seiten zu Belegen zusammenstellen' })).toBeInTheDocument();
});
