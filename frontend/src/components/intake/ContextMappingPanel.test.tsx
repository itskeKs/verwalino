import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ContextMappingPanel } from './ContextMappingPanel';
import type { ContextMapping, MappingOptions } from '../../api/contextMappings';
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const saved: ContextMapping = { id: 'v1', property_id: 'property', scope_id: 'scope', identifier_kind: 'property_id', identifier_value: 'property', revision_number: 1, state: 'active', valid_from: '2026-01-01', valid_to: '2027-12-31', policy: 'manual-context-reuse-v1' };
const options: MappingOptions = { allocation_set_id: 'allocation', can_activate: true, scope_label: 'Reviewed WEG', options: [{ kind: 'property_id', value: 'property', page_number: 1, quotation: 'Objekt-ID: property', method: 'text_layer', current: null }] };
const props = { allocationId: 'allocation', parentGeneration: 0, parentBusy: false };
async function fill() {
  fireEvent.click(await screen.findByRole('button', { name: 'Kennung auswählen: property' }));
  await waitFor(() => expect(screen.getByLabelText('Leistungszeitraum gültig ab')).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Leistungszeitraum gültig ab'), { target: { value: '2026-01-01' } });
  fireEvent.change(screen.getByLabelText('Leistungszeitraum gültig bis'), { target: { value: '2027-12-31' } });
  fireEvent.change(screen.getByLabelText('Begründung der wiederverwendbaren Zuordnung'), { target: { value: 'Reviewed private source proof' } });
  fireEvent.click(screen.getByRole('checkbox'));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
it('requires explicit identifier, blank dates and unchecked consent before separate saving', async () => {
  const requests: RequestInit[] = [];
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json(saved, 201); } return json(options); }));
  render(<ContextMappingPanel {...props} />); await screen.findByText(/Seite 1/); expect(requests).toHaveLength(0);
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Kennung auswählen: property' }));
  expect(await screen.findByLabelText('Leistungszeitraum gültig ab')).toHaveValue(''); expect(screen.getByRole('checkbox')).not.toBeChecked();
  await fill();
  fireEvent.change(screen.getByLabelText('Leistungszeitraum gültig bis'), { target: { value: '2028-12-31' } });
  expect(screen.getByRole('checkbox')).not.toBeChecked(); fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Zuordnung zur Wiederverwendung speichern' }));
  await screen.findByText(/Speicherung bestätigt/);
  expect(JSON.parse(String(requests[0].body))).toMatchObject({ allocation_set_id: 'allocation', valid_from: '2026-01-01', valid_to: '2028-12-31', reuse_consented: true, page_number: 1 });
  expect(new Headers(requests[0].headers).get('If-Match')).toBe('"none"');
});
it('preserves exact unknown request while its source becomes stale or parent result disappears', async () => {
  const requests: RequestInit[] = []; let stale = false;
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => { if (init?.method === 'POST') { requests.push(init); return json({}, 503); } return json(stale ? { ...options, can_activate: false, options: [] } : options); }));
  const rendered = render(<ContextMappingPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung zur Wiederverwendung speichern' }));
  await screen.findByText(/Keine bestätigte Antwort/); stale = true;
  rendered.rerender(<ContextMappingPanel {...props} allocationId={null} parentGeneration={1} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Dieselbe Zuordnung erneut speichern' })).toBeEnabled());
  expect(screen.getByLabelText('Leistungszeitraum gültig ab')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Dieselbe Zuordnung erneut speichern' })); await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[0].body).toBe(requests[1].body);
  for (const key of ['Idempotency-Key', 'If-Match']) expect(new Headers(requests[0].headers).get(key)).toBe(new Headers(requests[1].headers).get(key));
});
it('clears a denied retained request and reauthorizes its identifier after refresh', async () => {
  let deny = false; const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => { urls.push(url); return init?.method === 'POST' ? json({}, 503) : deny ? json({}, 404) : json(options); }));
  const rendered = render(<ContextMappingPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung zur Wiederverwendung speichern' })); await screen.findByText(/Keine bestätigte Antwort/);
  deny = true; rendered.rerender(<ContextMappingPanel {...props} parentGeneration={1} />);
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Dieselbe Zuordnung erneut speichern' })).not.toBeInTheDocument());
  expect(screen.queryByDisplayValue('Reviewed private source proof')).not.toBeInTheDocument();
  expect(urls.some(url => url.includes('identifier_kind=property_id&identifier_value=property'))).toBe(true);
});
it('requires new identifier selection and consent after predecessor changes', async () => {
  let current = options; vi.stubGlobal('fetch', vi.fn(async () => json(current)));
  const rendered = render(<ContextMappingPanel {...props} />); await fill();
  current = { ...options, options: [{ ...options.options[0], current: saved }] };
  rendered.rerender(<ContextMappingPanel {...props} parentGeneration={1} />);
  await screen.findByText(/Grundlage geändert/); expect(screen.getByRole('button', { name: 'Zuordnung zur Wiederverwendung speichern' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Kennung auswählen: property' }));
  expect(screen.getByRole('checkbox')).not.toBeChecked(); expect(screen.getByLabelText('Leistungszeitraum gültig ab')).toHaveValue('');
});
it('reauthorizes a saved receipt after the parent disappears and clears actual denial', async () => {
  let deny = false; const urls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    urls.push(url); if (init?.method === 'POST') return json(saved, 201);
    return deny ? json({}, 404) : json(options);
  }));
  const rendered = render(<ContextMappingPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung zur Wiederverwendung speichern' }));
  await screen.findByText(/Speicherung bestätigt/); deny = true;
  rendered.rerender(<ContextMappingPanel {...props} allocationId={null} parentGeneration={1} />);
  await waitFor(() => expect(screen.queryByText(/Speicherung bestätigt/)).not.toBeInTheDocument());
  expect(urls.at(-1)).toContain('/allocation/mapping-options?identifier_kind=property_id&identifier_value=property');
});
it('clears the previous receipt before reauthorizing another identifier on the same source', async () => {
  const meter = { ...options.options[0], kind: 'meter_number' as const, value: 'M-123', quotation: 'Zählernummer: M-123' };
  let hidePrevious = false;
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST' ? json(saved, 201)
    : json({ ...options, options: hidePrevious ? [meter] : [...options.options, meter] })));
  render(<ContextMappingPanel {...props} />); await fill(); fireEvent.click(screen.getByRole('button', { name: 'Zuordnung zur Wiederverwendung speichern' }));
  await screen.findByText(/Speicherung bestätigt/); hidePrevious = true;
  fireEvent.click(screen.getByRole('button', { name: 'Kennung auswählen: M-123' }));
  await screen.findByText('Ausgewählt: M-123');
  expect(screen.queryByText(/Speicherung bestätigt/)).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Aktuellen Zustand prüfen oder widerrufen' })).not.toBeInTheDocument();
});
