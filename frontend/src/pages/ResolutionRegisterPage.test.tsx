import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ResolutionRegisterPage } from './ResolutionRegisterPage';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('opens the property register at the entry referenced by the reserve ledger', async () => {
  const scroll = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url === '/api/properties'
    ? [{ id: 'property-1', name: 'WEG Gartenstraße', management_type: 'WEG' }]
    : [{ id: 'entry-42', sequence_number: 42, resolution_date: '2026-06-15', entry_date: '2026-11-01', wording: 'Gartenreparatur aus der Rücklage', location: 'Berlin' }]),
  { headers: { 'Content-Type': 'application/json' } })));
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  render(<MemoryRouter initialEntries={['/beschluss-sammlung?property_id=property-1#beschluss-entry-42']}><ResolutionRegisterPage /></MemoryRouter>);
  expect(await screen.findByText('Gartenreparatur aus der Rücklage')).toBeInTheDocument();
  expect(screen.getByText('42').closest('tr')).toHaveAttribute('id', 'beschluss-entry-42');
  await waitFor(() => expect(scroll).toHaveBeenCalledOnce());
});


it('shows the meeting date first and preserves the later entry date', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url === '/api/properties'
    ? [{ id: 'property-1', name: 'Gartenstraße 5', management_type: 'WEG' }]
    : [{ id: 'entry-1', sequence_number: 1, resolution_date: '2026-06-15', entry_date: '2026-11-01', wording: 'Gartenpflege', location: 'Gemeinschaftsraum' }]),
  { headers: { 'Content-Type': 'application/json' } })));
  render(<MemoryRouter initialEntries={['/beschluss-sammlung?property_id=property-1']}><ResolutionRegisterPage /></MemoryRouter>);
  await screen.findByText('Gartenpflege');
  const headings = screen.getAllByRole('columnheader').map(h => h.textContent);
  expect(headings.indexOf('Datum der Versammlung')).toBeGreaterThan(-1);
  expect(headings.indexOf('Eingetragen am')).toBe(headings.indexOf('Datum der Versammlung') + 1);
  const cells = screen.getByText('Gartenpflege').closest('tr')!.querySelectorAll('td');
  expect(cells[1]).toHaveTextContent('15.06.2026');
  expect(cells[2]).toHaveTextContent('01.11.2026');
});


it('shows disclosed levy summaries and links to the levy page', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url === '/api/properties'
    ? [{ id: 'p', name: 'Gartenstraße 5', management_type: 'WEG' }]
    : [{ id: 'entry', property_id: 'p', sequence_number: 1, resolution_date: '2026-05-15', entry_date: '2026-05-16', wording: 'Dachreparatur', location: 'Berlin',
        special_levies: [{ id: 'levy', amount: '1200.00', due_date: '2026-07-01', accounting_scope_id: 'scope' }] }]),
  { headers: { 'Content-Type': 'application/json' } })));
  render(<MemoryRouter initialEntries={['/beschluss-sammlung?property_id=p']}><ResolutionRegisterPage /></MemoryRouter>);
  expect(await screen.findByRole('link', { name: /Sonderumlage 1\.200,00\s€, fällig 01\.07\.2026/ })).toHaveAttribute('href', '/sonderumlagen?property_id=p&accounting_scope_id=scope#sonderumlage-levy');
});
