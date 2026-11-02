import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
const sourceJson = (url: string, fallback: unknown) => json(url.includes('cost-entries') ? [{ id: 'cost-1', document_number: 'RE-1', gross_amount: '1200.00', status: 'confirmed', superseded_by_id: null }]
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
  fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'PAYMENT_ASSIGNMENT' } });
  fireEvent.change(screen.getByLabelText('Zahlung ohne Planbezug'), { target: { value: 'unknown-cash' } });
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '600,00' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Beleg und Einheit geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: /^Speichern$/ }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
    body: JSON.stringify({ component: 'CONTRIBUTION', amount: '600.00', reason: 'Beleg und Einheit geprüft', payment_id: 'unknown-cash' }),
  })));
  await waitFor(() => expect(screen.queryByText('Ohne Planbezug')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  expect(screen.getByLabelText('Einheit *')).toBeRequired();
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

it('keeps the finalized correction control disabled with its German reason', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...ledger, frozen: true })));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="finalized" onChanged={() => {}} />);
  await screen.findByText(/Finalisierter Rücklagenstand/);
  expect(screen.getByRole('button', { name: 'Korrektur erfassen' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Korrektur erfassen' })).toHaveAccessibleDescription('Die Rücklage dieses Zeitraums ist finalisiert.');
});

it('does not call a computed draft a finalized snapshot when its ledger is frozen', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...ledger, frozen: true })));
  const view = render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" refreshKey={0} onChanged={() => {}} />);
  await screen.findByText(/21\.000,00/);
  view.rerender(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" refreshKey={1} onChanged={() => {}} />);
  await screen.findByText(/21\.000,00/);
  expect(screen.queryByText(/Finalisierter Rücklagenstand/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Korrektur erfassen' })).toBeDisabled();
});

it.each([['20500,00', '+500,00', '20500.00'], ['19.700,00', '-300,00', '19700.00']])('sends opening total %s and the seen opening while previewing the difference', async (total, preview, normalized) => {
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST' ? json({ id: 'opening-1' }, 201) : json(ledger));
  vi.stubGlobal('fetch', fetchMock);
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'OPENING' } });
  expect(screen.getByRole('option', { name: 'Anfangsbestand korrigieren' })).toHaveValue('OPENING');
  expect(screen.getByRole('dialog')).toHaveAccessibleName('Anfangsbestand korrigieren');
  const input = screen.getByLabelText('Korrigierter Anfangsbestand (gesamt) *');
  expect(input).toHaveValue('20000,00');
  expect(screen.getByText(/Differenz:/)).toHaveTextContent('Differenz: 0,00');
  fireEvent.change(input, { target: { value: total } });
  expect(screen.getByText(/Differenz:/)).toHaveTextContent(`Differenz: ${preview}`);
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Bankunterlagen geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ body: JSON.stringify({
    component: 'OPENING', corrected_opening_balance: normalized, reason: 'Bankunterlagen geprüft', expected_opening_balance: '20000.00',
  }) })));
});

it('uses the app German message instead of native validation for empty correction inputs', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => sourceJson(url, ledger)));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur erfassen' }));
  expect(screen.getByRole('button', { name: 'Speichern' }).closest('form')).toHaveAttribute('novalidate');
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Bitte geben Sie einen Betrag mit höchstens zwei Nachkommastellen und eine Begründung ein.');
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

it('filters funding evidence and retains selected values after sources become invalid', async () => {
  let invalid = false;
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.includes('cost-entries') ? [
    { id: 'cost-1', status: 'confirmed', superseded_by_id: invalid ? 'cost-2' : null, document_number: 'RE-1', gross_amount: '4000.00' },
    { id: 'cost-old', status: 'confirmed', superseded_by_id: 'cost-1', document_number: 'Alt', gross_amount: '4000.00' },
    { id: 'cost-draft', status: 'draft', document_number: 'Entwurf', gross_amount: '4000.00' },
  ] : url.includes('/meetings/register/') ? [
    { id: 'register-1', result: invalid ? 'ABGELEHNT' : 'ANGENOMMEN', sequence_number: 1, wording: 'Reparatur' },
    { id: 'register-rejected', result: 'ABGELEHNT', sequence_number: 2, wording: 'Abgelehnt' },
  ] : url.includes('/payments') ? [] : ledger)));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'FUNDING' } });
  const cost = await screen.findByLabelText('Bestätigter Kostenbeleg *');
  const register = screen.getByLabelText('Beschluss-Sammlung *');
  expect(within(cost).getAllByRole('option').map(row => row.getAttribute('value'))).toEqual(['', 'cost-1']);
  expect(within(register).getAllByRole('option').map(row => row.getAttribute('value'))).toEqual(['', 'register-1']);
  fireEvent.change(cost, { target: { value: 'cost-1' } });
  fireEvent.change(register, { target: { value: 'register-1' } });
  fireEvent.click(screen.getByRole('button', { name: 'Abbrechen' }));
  invalid = true;
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur erfassen' }));
  const retained = await screen.findByLabelText('Bestätigter Kostenbeleg *');
  expect(retained).toHaveValue('cost-1');
  expect(screen.getByLabelText('Beschluss-Sammlung *')).toHaveValue('register-1');
  expect(within(retained).getAllByRole('option').map(row => row.getAttribute('value'))).toEqual(['', 'cost-1']);
});


it('requires a unit and reason, shows remaining headroom and retains inputs on refusal', async () => {
  const message = 'Sie können dieser Einheit noch höchstens 1.234,56 € als Zuführung zuordnen.';
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ detail: { code: 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED', message } }, 422)
    : json({ ...ledger, contribution_headroom: [{ unit_id: 'unit-1', unit_number: 'Wohnung Änne Groß mit langem Namen', remaining: '1234.56' }] }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'CONTRIBUTION' } });
  expect(screen.queryByText('Allgemeine Zuführungskorrektur')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Bestätigter Kostenbeleg *')).not.toBeInTheDocument();
  const unit = await screen.findByLabelText('Einheit *');
  expect(unit).toBeRequired();
  fireEvent.change(unit, { target: { value: 'unit-1' } });
  expect(screen.getByText(/Noch zuordenbar:/)).toHaveTextContent('1.234,56');
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '1234,57' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Hausgeld geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(unit).toHaveValue('unit-1');
  expect(screen.getByLabelText('Betrag *')).toHaveValue('1234,57');
  expect(screen.getByLabelText('Begründung *')).toHaveValue('Hausgeld geprüft');
  expect(fetchMock).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ body: JSON.stringify({
    component: 'CONTRIBUTION', amount: '1234.57', reason: 'Hausgeld geprüft', unit_id: 'unit-1',
  }) }));
});

it.each([
  ['ADJUSTMENT', 'RESERVE_ADJUSTMENT_EXCEEDS_WITHDRAWALS', 'Sie können die Entnahmen dieses Kostenbelegs noch um höchstens 1.000,00 € vermindern.'],
  ['WITHDRAWAL', 'RESERVE_PAYOUT_EVIDENCE_REQUIRED', 'Sie können diesem Kostenbeleg noch höchstens 300,00 € als Rücklagenentnahme zuordnen.'],
])('shows the German allowed amount for %s refusals and keeps the evidence', async (component, code, message) => {
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ detail: { code, message } }, 422) : sourceJson(url, ledger)));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Art'), { target: { value: component } });
  await chooseEvidence();
  fireEvent.change(screen.getByLabelText('Betrag *'), { target: { value: '1000,01' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Belegte Entnahmen geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(screen.getByLabelText('Bestätigter Kostenbeleg *')).toHaveValue('cost-1');
  expect(screen.getByLabelText('Beschluss-Sammlung *')).toHaveValue('register-1');
});


it.each(['reduce', 'delete'])('audits a draft contribution %s with a mandatory reason', async operation => {
  const changed = vi.fn();
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST' || init?.method === 'DELETE'
    ? json({ id: 'reversal-1' }, 201) : json({ ...ledger, entries: [
      { id: 'correction-1', kind: 'OVERRIDE', component: 'CONTRIBUTION', date: '2025-12-31',
        amount: '800.00', current_amount: '500.00', unit_id: 'unit-1', unit_number: 'Änne Groß mit langem Namen', reason: 'Hausgeld geprüft' },
    ] }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={changed} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur verringern / löschen' }));
  expect(screen.getByLabelText('Verbleibender Korrekturbetrag *')).toHaveValue('500,00');
  fireEvent.click(screen.getByRole('button', { name: operation === 'delete' ? 'Korrektur löschen' : 'Korrektur verringern' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Bitte begründen Sie die Rücklagenkorrektur.');
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Hausgeld storniert und abgeglichen' } });
  fireEvent.change(screen.getByLabelText('Verbleibender Korrekturbetrag *'), { target: { value: '200,00' } });
  fireEvent.click(screen.getByRole('button', { name: operation === 'delete' ? 'Korrektur löschen' : 'Korrektur verringern' }));
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
  expect(fetchMock).toHaveBeenCalledWith(`/api/weg-settlements/run-1/reserve-ledger/overrides/correction-1${operation === 'reduce' ? '/reduce' : ''}`,
    expect.objectContaining({ method: operation === 'reduce' ? 'POST' : 'DELETE', body: JSON.stringify(operation === 'reduce'
      ? { reduced_to: '200.00', reason: 'Hausgeld storniert und abgeglichen' } : { reason: 'Hausgeld storniert und abgeglichen' }) }));
});

it('hides contribution reduction controls on finalized and reversed entries', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...ledger, frozen: true, entries: [
    { id: 'original', kind: 'OVERRIDE', component: 'CONTRIBUTION', date: '2025-12-31', amount: '500.00', current_amount: '500.00', unit_id: 'unit-1' },
  ] })));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="finalized" onChanged={() => {}} />);
  await screen.findByText('Begründete Korrektur · Zuführungskorrektur');
  expect(screen.queryByRole('button', { name: 'Korrektur verringern / löschen' })).not.toBeInTheDocument();
});

it('retains the reduced amount and reason after a booked-headroom refusal', async () => {
  const message = 'Sie können für diese Korrektur höchstens 200,00 € beibehalten.';
  vi.stubGlobal('fetch', vi.fn(async (_url: string, init?: RequestInit) => init?.method === 'POST'
    ? json({ detail: { code: 'RESERVE_CONTRIBUTION_EXCEEDS_BOOKED', message } }, 422)
    : json({ ...ledger, entries: [{ id: 'correction-1', kind: 'OVERRIDE', component: 'CONTRIBUTION',
      date: '2025-12-31', amount: '500.00', current_amount: '500.00', unit_id: 'unit-1', unit_number: '1' }] })));
  render(<ReserveLedgerPanel runId="run-1" propertyId="property-1" status="draft" onChanged={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur verringern / löschen' }));
  fireEvent.change(screen.getByLabelText('Verbleibender Korrekturbetrag *'), { target: { value: '300,00' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Hausgeld abgeglichen' } });
  fireEvent.click(screen.getByRole('button', { name: 'Korrektur verringern' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(screen.getByLabelText('Verbleibender Korrekturbetrag *')).toHaveValue('300,00');
  expect(screen.getByLabelText('Begründung *')).toHaveValue('Hausgeld abgeglichen');
});
