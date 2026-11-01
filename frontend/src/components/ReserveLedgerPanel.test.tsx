import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReserveLedgerPanel } from './ReserveLedgerPanel';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it('distinguishes withdrawal corrections from funded cash and links readable evidence', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...ledger, entries: [
    { kind: 'CONTRIBUTION', date: '2025-01-15', amount: '1000.00', payment_id: 'payment-1',
      payment_note: 'Hausgeld Januar', owner_name: 'Änne Groß mit langem Familiennamen' },
    { kind: 'WITHDRAWAL', date: '2025-02-01', amount: '1200.00', payment_id: 'payout-1',
      payment_note: 'Gartenrechnung', cost_entry_id: 'cost-1', cost_document_number: 'RE-2025-123456789',
      register_entry_id: 'register-1', register_sequence_number: 42 },
    { kind: 'OVERRIDE', component: 'WITHDRAWAL', date: '2025-02-02', amount: '-1.00', reason: 'Entnahme berichtigt' },
    { kind: 'LATE_CASH', component: 'WITHDRAWAL', date: '2024-12-31', amount: '-100.00', payment_id: 'late-payout' },
  ] })));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  expect(await screen.findByText('Eigentümer: Änne Groß mit langem Familiennamen')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Zahlung: Hausgeld Januar' })).toHaveAttribute('href', '/zahlungen/payment-1');
  expect(screen.getByRole('link', { name: 'Kostenbeleg: RE-2025-123456789' })).toHaveAttribute('href', '/kosten/cost-1');
  expect(screen.getByRole('link', { name: 'Beschluss Nr. 42' })).toHaveAttribute('href', '/beschluss-sammlung?property_id=property-1#beschluss-register-1');
  expect(screen.getByText('Finanzierte Entnahme')).toBeInTheDocument();
  expect(screen.getByText('Begründete Korrektur · Entnahmekorrektur')).toBeInTheDocument();
  expect(screen.getByText('Nachträgliche Buchungen zum abgeschlossenen Jahr · Finanzierte Entnahme')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  expect(screen.getByRole('option', { name: 'Entnahmekorrektur' })).toHaveValue('WITHDRAWAL');
});

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
const sourceJson = (url: string, fallback: unknown) => json(url.includes('cost-entries') ? [{ id: 'cost-1', document_number: 'RE-1', gross_amount: '1200.00' }]
  : url.includes('/meetings/register/') ? [{ id: 'register-1', sequence_number: 1, wording: 'Reparatur', result: 'ANGENOMMEN' }] : fallback);
async function chooseEvidence() {
  const cost = await screen.findByLabelText('Bestätigter Kostenbeleg *');
  await waitFor(() => expect(cost).not.toBeDisabled());
  fireEvent.change(cost, { target: { value: 'cost-1' } });
  fireEvent.change(screen.getByLabelText('Beschluss-Sammlung *'), { target: { value: 'register-1' } });
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

it('shows unassigned Hausgeld at its full amount and saves an audited payment assignment', async () => {
  let assigned = false;
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { assigned = true; return json({ id: 'assignment-1' }, 201); }
    return sourceJson(_url, assigned ? ledger : { ...ledger, unlinked_payment_ids: ['unknown-cash'],
      entries: [{ kind: 'UNASSIGNED', date: '2025-08-05', amount: '1300.00', payment_id: 'unknown-cash' }] });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  await screen.findByText('Ohne Planbezug');
  expect(screen.getByRole('alert')).toHaveTextContent('Planbezug');
  expect(screen.getByText(/1\.300,00/, { selector: 'td' })).toBeInTheDocument();
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
  await chooseEvidence();
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '5,00' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Weitere Zuführung geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: /^Speichern$/ }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
    body: JSON.stringify({ component: 'CONTRIBUTION', amount: '5.00', reason: 'Weitere Zuführung geprüft', cost_entry_id: 'cost-1', register_entry_id: 'register-1' }),
  })));
});

it('separates cash from planning and saves a reasoned signed correction', async () => {
  const changed = vi.fn();
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ id: 'override-1' }, 201) : sourceJson(_url, ledger));
  vi.stubGlobal('fetch', fetchMock);
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={changed} />);
  await screen.findByText(/21\.000,00/);
  expect(screen.getByText('Geplante Zuführung (Soll)')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  await chooseEvidence();
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '-12,34' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Berichtigter Zinsabschluss' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  const call = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
  expect(JSON.parse(call[1]!.body as string)).toEqual({ component: 'ADJUSTMENT', amount: '-12.34', reason: 'Berichtigter Zinsabschluss', cost_entry_id: 'cost-1', register_entry_id: 'register-1' });
});

it('keeps the reason and amount after refusal and flags an overdrawn balance', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ detail: { code: 'RESERVE_PERIOD_FINALIZED', message: 'Die Rücklage ist finalisiert.' } }, 409) : sourceJson(_url, { ...ledger, overdrawn: true, closing_balance: '-1.00' })));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  await screen.findByText(/Rücklage ist überzogen/);
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  await chooseEvidence();
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


it('requires cost and resolution for typed withdrawal corrections', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('reserve-ledger') ? ledger : [])));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'WITHDRAWAL' } });
  expect(await screen.findByLabelText('Bestätigter Kostenbeleg *')).toBeRequired();
  expect(screen.getByLabelText('Beschluss-Sammlung *')).toBeRequired();
  expect(screen.queryByLabelText('Gebuchte Auszahlung *')).not.toBeInTheDocument();
});
