import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReserveLedgerPanel } from './ReserveLedgerPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('shows late closed-year cash separately with its original value date', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...ledger, late_cash_total: '999.00', closing_balance: '21999.00',
    entries: [{ kind: 'LATE_CASH', component: 'CONTRIBUTION', date: '2025-12-31', amount: '999.00', payment_id: 'late-payment' }] })));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  await screen.findByText('31.12.2025');
  expect(screen.getAllByText(/Nachträgliche Buchungen zum abgeschlossenen Jahr/)).toHaveLength(2);
  expect(screen.getByText(/21\.999,00/)).toBeInTheDocument();
});
const ledger = { opening_balance: '20000.00', contributions_total: '1000.00', withdrawals_total: '0.00',
  overrides_total: '0.00', closing_balance: '21000.00', planned_contributions_total: '6000.00',
  overdrawn: false, frozen: false, booking_basis: 'CASH', entries: [
    { kind: 'CONTRIBUTION', date: '2025-01-15', amount: '1000.00', payment_id: 'payment-1' },
  ] };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

it('shows unassigned Hausgeld at its full amount and saves an audited payment assignment', async () => {
  let assigned = false;
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { assigned = true; return json({ id: 'assignment-1' }, 201); }
    return json(assigned ? ledger : { ...ledger, unlinked_payment_ids: ['unknown-cash'],
      entries: [{ kind: 'UNASSIGNED', date: '2025-08-05', amount: '1300.00', payment_id: 'unknown-cash' }] });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  await screen.findByText('Ohne Planbezug');
  expect(screen.getByRole('alert')).toHaveTextContent('Planbezug');
  expect(screen.getByText(/1\.300,00/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'CONTRIBUTION' } });
  fireEvent.change(screen.getByLabelText('Zahlung ohne Planbezug'), { target: { value: 'unknown-cash' } });
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '600,00' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Beleg und Einheit geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: /^Speichern$/ }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
    body: JSON.stringify({ component: 'CONTRIBUTION', amount: '600.00', reason: 'Beleg und Einheit geprüft', payment_id: 'unknown-cash' }),
  })));
  await waitFor(() => expect(screen.queryByText('Ohne Planbezug')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '5,00' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Weitere Zuführung geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: /^Speichern$/ }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
    body: JSON.stringify({ component: 'CONTRIBUTION', amount: '5.00', reason: 'Weitere Zuführung geprüft' }),
  })));
});

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
