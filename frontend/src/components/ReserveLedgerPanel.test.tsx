import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReserveLedgerPanel } from './ReserveLedgerPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const ledger = { opening_balance: '20000.00', contributions_total: '1000.00', withdrawals_total: '0.00',
  overrides_total: '0.00', closing_balance: '21000.00', planned_contributions_total: '6000.00',
  overdrawn: false, frozen: false, booking_basis: 'CASH', entries: [
    { kind: 'CONTRIBUTION', date: '2025-01-15', amount: '1000.00', payment_id: 'payment-1' },
  ] };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

it('separates cash from planning and saves a reasoned signed correction', async () => {
  const changed = vi.fn();
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ id: 'override-1' }, 201) : json(ledger));
  vi.stubGlobal('fetch', fetchMock);
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={changed} />);
  await screen.findByText(/21\.000,00/);
  expect(screen.getByText('Geplante Zuführung (Soll)')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '-12,34' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Berichtigter Zinsabschluss' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(call[1]!.body as string)).toEqual({ component: 'ADJUSTMENT', amount: '-12.34', reason: 'Berichtigter Zinsabschluss' });
});

it('keeps the reason and amount after refusal and flags an overdrawn balance', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ detail: 'Die Rücklage ist finalisiert.' }, 409) : json({ ...ledger, overdrawn: true, closing_balance: '-1.00' })));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  await screen.findByText(/Rücklage ist überzogen/);
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Beleg geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await screen.findByText('Die Rücklage ist finalisiert.');
  expect(screen.getByLabelText('Begründung *')).toHaveValue('Beleg geprüft');
});

it('shows the frozen ledger without an override action', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...ledger, frozen: true })));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="finalized" onChanged={() => {}} />);
  await screen.findByText(/Finalisierter Rücklagenstand/);
  expect(screen.queryByRole('button', { name: 'Korrektur erfassen' })).not.toBeInTheDocument();
});
