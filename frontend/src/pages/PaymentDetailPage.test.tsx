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
    if (url.startsWith('/api/cost-entries')) return json([{ id: 'cost', document_number: 'RE-Ä-123', gross_amount: '1200.00' }]);
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
