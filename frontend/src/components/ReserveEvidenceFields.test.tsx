import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { ReserveEvidenceFields } from './ReserveEvidenceFields';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const costs = [
  { id: 'confirmed', status: 'confirmed', superseded_by_id: null },
  { id: 'draft', status: 'draft', superseded_by_id: null },
  { id: 'superseded', status: 'confirmed', superseded_by_id: 'confirmed' },
].map(row => ({ ...row, document_number: row.id, gross_amount: '4000.00' }));
const entries = [
  { id: 'accepted', result: 'ANGENOMMEN' },
  { id: 'rejected', result: 'ABGELEHNT' },
].map((row, index) => ({ ...row, sequence_number: index + 1, wording: row.id }));

function mockSources() {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => new Response(JSON.stringify(
    url.includes('cost-entries') ? costs : entries), { headers: { 'Content-Type': 'application/json' } })));
}

it('offers only current confirmed costs and accepted resolutions', async () => {
  mockSources();
  render(<ReserveEvidenceFields propertyId="property" cost="" register="" onChange={() => {}} />);
  const cost = screen.getByLabelText('Bestätigter Kostenbeleg *');
  const register = screen.getByLabelText('Beschluss-Sammlung *');
  await waitFor(() => expect(cost).not.toBeDisabled());
  expect(within(cost).getAllByRole('option').map(row => row.getAttribute('value'))).toEqual(['', 'confirmed']);
  expect(within(register).getAllByRole('option').map(row => row.getAttribute('value'))).toEqual(['', 'accepted']);
});

it.each(['draft', 'superseded'])('retains a linked %s cost and rejected resolution as the current values', async selected => {
  mockSources();
  render(<ReserveEvidenceFields propertyId="property" cost={selected} register="rejected" onChange={() => {}} />);
  const cost = screen.getByLabelText('Bestätigter Kostenbeleg *');
  const register = screen.getByLabelText('Beschluss-Sammlung *');
  await waitFor(() => expect(cost).not.toBeDisabled());
  expect(cost).toHaveValue(selected);
  expect(register).toHaveValue('rejected');
  expect(within(cost).getAllByRole('option').map(row => row.getAttribute('value'))).toEqual(['', 'confirmed', selected]);
});
