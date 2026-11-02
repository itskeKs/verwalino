import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ResolutionRegisterPage } from './ResolutionRegisterPage';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('opens the property register at the entry referenced by the reserve ledger', async () => {
  const scroll = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(url === '/api/properties'
    ? [{ id: 'property-1', name: 'WEG Gartenstraße', management_type: 'WEG' }]
    : [{ id: 'entry-42', sequence_number: 42, entry_date: '2025-02-01', wording: 'Gartenreparatur aus der Rücklage', location: 'Berlin' }]),
  { headers: { 'Content-Type': 'application/json' } })));
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: scroll });
  render(<MemoryRouter initialEntries={['/beschluss-sammlung?property_id=property-1#beschluss-entry-42']}><ResolutionRegisterPage /></MemoryRouter>);
  expect(await screen.findByText('Gartenreparatur aus der Rücklage')).toBeInTheDocument();
  expect(screen.getByText('42').closest('tr')).toHaveAttribute('id', 'beschluss-entry-42');
  await waitFor(() => expect(scroll).toHaveBeenCalledOnce());
});
