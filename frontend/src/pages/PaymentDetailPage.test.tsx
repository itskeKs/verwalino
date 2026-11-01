import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PaymentDetailPage } from './PaymentDetailPage';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });

it('links both references on an imported payout draft before booking', async () => {
  let payment = { id: 'payout', bank_transaction_id: 'transaction', property_id: 'property', bank_account_id: 'bank', payment_type: 'RUECKLAGENENTNAHME',
    amount: '1200.00', direction: 'OUT', value_date: '2026-01-03', status: 'draft',
    reserve_cost_entry_id: null as string | null, reserve_register_entry_id: null as string | null };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'PATCH') { payment = { ...payment, ...JSON.parse(init.body as string) }; return json(payment); }
    if (url.endsWith('/book')) { payment = { ...payment, status: 'booked' }; return json(payment); }
    if (url === '/api/payments/payout') return json(payment);
    if (url.startsWith('/api/cost-entries')) return json([{ id: 'cost', document_number: 'RE-Ä-123', gross_amount: '1200.00', status: 'confirmed', superseded_by_id: null }]);
    if (url.startsWith('/api/meetings/register/')) return json([{ id: 'resolution', sequence_number: 42, wording: 'Dachreparatur', result: 'ANGENOMMEN' }]);
    return json([]);
  });
  vi.stubGlobal('fetch', fetchMock);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(<MemoryRouter initialEntries={['/zahlungen/payout']}><Routes><Route path="/zahlungen/:paymentId" element={<PaymentDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByRole('alert')).toHaveTextContent('bleibt ein Entwurf');
  expect(screen.queryByRole('button', { name: 'Entwurf verwerfen' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Bearbeiten' }));
  const cost = await screen.findByLabelText('Bestätigter Kostenbeleg *');
  await waitFor(() => expect(cost).not.toBeDisabled());
  fireEvent.change(cost, { target: { value: 'cost' } });
  fireEvent.change(screen.getByLabelText('Beschluss-Sammlung *'), { target: { value: 'resolution' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await screen.findByRole('button', { name: 'Buchen' });
  expect(payment.reserve_cost_entry_id).toBe('cost');
  expect(payment.reserve_register_entry_id).toBe('resolution');
  fireEvent.click(screen.getByRole('button', { name: 'Buchen' }));
  await waitFor(() => expect(payment.status).toBe('booked'));
});

it('confirms release of an imported payout and returns to its bank transactions', async () => {
  const payment = { id: 'payout', bank_transaction_id: 'transaction', bank_account_id: 'bank', property_id: 'property',
    payment_type: 'RUECKLAGENENTNAHME', amount: '1200.00', direction: 'OUT', value_date: '2025-02-03', status: 'draft',
    reserve_cost_entry_id: null, reserve_register_entry_id: null };
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => init?.method === 'DELETE'
    ? new Response(null, { status: 204 }) : json(url === '/api/payments/payout' ? payment : []));
  vi.stubGlobal('fetch', fetchMock);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  render(<MemoryRouter initialEntries={['/zahlungen/payout']}><Routes>
    <Route path="/zahlungen/:paymentId" element={<PaymentDetailPage />} />
    <Route path="/bankkonten/bank/umsaetze" element={<p>Bankumsätze · Neu</p>} />
  </Routes></MemoryRouter>);
  const release = await screen.findByRole('button', { name: 'Zuordnung lösen' });
  fireEvent.click(release);
  expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Neu'));
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  confirm.mockReturnValue(true);
  fireEvent.click(release);
  await screen.findByText('Bankumsätze · Neu');
  expect(fetchMock).toHaveBeenCalledWith('/api/payments/payout', expect.objectContaining({ method: 'DELETE' }));
});

it('retains the imported draft and displays the bound refusal after a release attempt', async () => {
  const payment = { id: 'payout', bank_transaction_id: 'transaction', bank_account_id: 'bank', property_id: 'property',
    payment_type: 'RUECKLAGENENTNAHME', amount: '1200.00', direction: 'OUT', value_date: '2025-02-03', status: 'draft' };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'DELETE'
    ? new Response(JSON.stringify({ detail: { code: 'RESERVE_IMPORTED_DRAFT_BOUND', message: 'Diese Zuordnung bleibt gebunden.' } }), { status: 409 })
    : json(url === '/api/payments/payout' ? payment : [])));
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  render(<MemoryRouter initialEntries={['/zahlungen/payout']}><Routes><Route path="/zahlungen/:paymentId" element={<PaymentDetailPage />} /></Routes></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Zuordnung lösen' }));
  await screen.findByText('Diese Zuordnung bleibt gebunden.');
  expect(screen.getByRole('button', { name: 'Zuordnung lösen' })).toBeEnabled();
});

it.each(['booked', 'evidenced'])('offers no release for a %s imported payout', async state => {
  const payment = { id: 'payout', bank_transaction_id: 'transaction', bank_account_id: 'bank', property_id: 'property',
    payment_type: 'RUECKLAGENENTNAHME', amount: '1200.00', direction: 'OUT', value_date: '2025-02-03',
    status: state === 'booked' ? 'booked' : 'draft', reserve_cost_entry_id: 'cost', reserve_register_entry_id: 'register' };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url === '/api/payments/payout' ? payment : [])));
  render(<MemoryRouter initialEntries={['/zahlungen/payout']}><Routes><Route path="/zahlungen/:paymentId" element={<PaymentDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByRole('heading', { name: /Zahlung/ });
  expect(screen.queryByRole('button', { name: 'Zuordnung lösen' })).not.toBeInTheDocument();
});
