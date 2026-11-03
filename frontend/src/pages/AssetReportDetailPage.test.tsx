import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AssetReportDetailPage } from "./AssetReportDetailPage";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('shows four plain reserve labels and German amounts in a finalized report while preserving other positions', async () => {
  const lines = [
    { line_type: 'RESERVE_OPENING', label: 'Erhaltungsruecklage Anfangsbestand', amount: '3000.00' },
    { line_type: 'RESERVE_CONTRIBUTION', label: 'Zufuehrung Erhaltungsruecklage', amount: '1234.56', note: 'Summe der Zufuehrungen im Zeitraum' },
    { line_type: 'RESERVE_WITHDRAWAL', label: 'Entnahme Erhaltungsruecklage', amount: '0.00' },
    { line_type: 'RESERVE_CLOSING', label: 'Erhaltungsruecklage Endbestand', amount: '4184.56' },
    { line_type: 'BANK_BALANCE', label: 'Bankkonto Giro (LAUFEND)', amount: '4234.56', note: 'Stichtagssaldo (Verwalter-Angabe, kein Buchungssaldo)' },
    { line_type: 'RECEIVABLE', label: 'Hausgeld Müller', amount: '100.00' },
    { line_type: 'PAYABLE', label: 'Rechnung Dachreparatur', amount: '250.00' },
    { line_type: 'RESERVE_ADJUSTMENT', label: 'Begründete Rücklagenkorrekturen', amount: '-50.00' },
  ].map((line, index) => ({ ...line, id: String(index) }));
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(
    url === '/api/properties' || url.includes('/bank-accounts') ? []
    : url.endsWith('/reserve-ledger') ? { entries: [], frozen: true }
    : { id: 'run-1', status: 'finalized', property_id: 'property-1', lines })));
  render(<MemoryRouter initialEntries={['/report/run-1']}><Routes><Route path="/report/:runId" element={<AssetReportDetailPage />} /></Routes></MemoryRouter>);
  const table = (await screen.findByRole('heading', { name: 'Vermögensaufstellung' })).parentElement!;
  expect(within(table).getAllByRole('columnheader', { name: 'Erhaltungsrücklage' })).toHaveLength(1);
  for (const [label, amount] of [['Anfangsbestand', '3.000,00 €'], ['Zuführungen', '1.234,56 €'], ['Entnahmen', '0,00 €'], ['Endbestand', '4.184,56 €']]) {
    const cell = within(table).getByText(label, { exact: true });
    expect(within(cell.closest('tr')!).getAllByRole('cell')[1]).toHaveTextContent(amount);
  }
  expect(table).not.toHaveTextContent(/Erhaltungsruecklage|Zufuehrung|RESERVE_/);
  expect(within(table).getByRole('row', { name: /Bankkonto.*— Bankkonto Giro \(LAUFEND\).*4234.56 EUR/ })).toHaveTextContent('Stichtagssaldo (Verwalter-Angabe, kein Buchungssaldo)');
  expect(within(table).getByRole('row', { name: /Forderung.*— Hausgeld Müller 100.00 EUR/ })).toBeInTheDocument();
  expect(within(table).getByRole('row', { name: /Verbindlichkeit.*— Rechnung Dachreparatur 250.00 EUR/ })).toBeInTheDocument();
  expect(within(table).getByRole('row', { name: 'Begründete Rücklagenkorrekturen -50.00 EUR' })).toBeInTheDocument();
});

it('keeps delete and ledger identities distinct without React console errors', async () => {
  const errors = vi.spyOn(console, 'error');
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(
    url === '/api/properties' || url.includes('/bank-accounts') ? []
    : url.endsWith('/reserve-ledger') ? { entries: [], frozen: false }
    : { id: 'run-keys', status: 'draft', property_id: 'property-1', lines: [], delete_restriction: null })));
  render(<MemoryRouter initialEntries={['/report/run-keys']}><Routes><Route path="/report/:runId" element={<AssetReportDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByRole('button', { name: 'Entwurf löschen' });
  expect(errors).not.toHaveBeenCalled();
});

it('refreshes deletion eligibility after saving a reserve opening declaration', async () => {
  let protectedOpening = false;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url === '/api/financial-input-coverage' && init?.method === 'POST') {
      protectedOpening = true; return json({ id: 'coverage' }, 201);
    }
    if (url === '/api/properties' || url.includes('/bank-accounts')) return json([]);
    if (url.endsWith('/reserve-ledger')) return json({ entries: [], frozen: false,
      opening_balance: protectedOpening ? '12500.00' : '0.00' });
    return json({ id: 'run-1', run_type: 'VERMOEGENSBERICHT', status: 'draft', property_id: 'property-1', lines: [],
      delete_restriction: protectedOpening ? { code: 'SETTLEMENT_DELETE_HAS_LEDGER', message: 'Entwürfe mit Rücklagenbuch-Einträgen können nicht gelöscht werden.' } : null });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<MemoryRouter initialEntries={['/report/run-1']}><Routes><Route path="/report/:runId" element={<AssetReportDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByRole('button', { name: 'Entwurf löschen' })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Rücklage Anfangsbestand'), { target: { value: '12500.00' } });
  fireEvent.change(screen.getByLabelText('Begründung der Rücklagenwerte'), { target: { value: 'Bankbestätigter Anfangsbestand' } });
  fireEvent.change(screen.getByLabelText('Grundlage der Prüfung'), { target: { value: 'Kontostände vollständig geprüft' } });
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'Datengrundlage speichern' }));
  await screen.findByText('Entwürfe mit Rücklagenbuch-Einträgen können nicht gelöscht werden.');
  expect(screen.queryByRole('button', { name: 'Entwurf löschen' })).not.toBeInTheDocument();
  await waitFor(() => expect(screen.getByText(/Aktueller Anfangsbestand:/)).toHaveTextContent('12.500,00'));
});

it('confirms an empty draft deletion and returns to the report list', async () => {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') return new Response(null, { status: 204 });
    if (url === '/api/properties' || url.includes('/bank-accounts')) return json([]);
    if (url.endsWith('/reserve-ledger')) return json({ entries: [], frozen: false });
    return json({ id: 'run-1', run_type: 'VERMOEGENSBERICHT', status: 'draft', property_id: 'property-1', lines: [], delete_restriction: null });
  });
  vi.stubGlobal('fetch', fetchMock);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  render(<MemoryRouter initialEntries={['/report/run-1']}><Routes><Route path="/report/:runId" element={<AssetReportDetailPage />} /><Route path="/vermoegensberichte" element={<p>Berichtsliste</p>} /></Routes></MemoryRouter>);
  fireEvent.click(await screen.findByRole('button', { name: 'Entwurf löschen' }));
  expect(confirm).toHaveBeenCalled();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(0);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole('button', { name: 'Entwurf löschen' }));
  await screen.findByText('Berichtsliste');
  expect(fetchMock).toHaveBeenCalledWith('/api/weg-settlements/asset-reports/run-1', expect.objectContaining({ method: 'DELETE' }));
  confirm.mockRestore();
});

it('shows the German deletion refusal and hides the action for a booked draft', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(
    url === '/api/properties' || url.includes('/bank-accounts') ? []
    : url.endsWith('/reserve-ledger') ? { entries: [], frozen: false }
    : { id: 'run-1', run_type: 'VERMOEGENSBERICHT', status: 'draft', property_id: 'property-1', lines: [], delete_restriction: {
      code: 'SETTLEMENT_DELETE_HAS_BOOKINGS', message: 'Entwürfe mit Buchungen können nicht gelöscht werden.' } })));
  render(<MemoryRouter initialEntries={['/report/run-1']}><Routes><Route path="/report/:runId" element={<AssetReportDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByText('Entwürfe mit Buchungen können nicht gelöscht werden.');
  expect(screen.queryByRole('button', { name: 'Entwurf löschen' })).not.toBeInTheDocument();
});

it("keeps missing reserve/account values blank and sends an explicitly reviewed zero", async () => {
  const bodies: Record<string, unknown>[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/reserve-ledger')) return json({ opening_balance: '0.00', contributions_total: '0.00', withdrawals_total: '0.00', overrides_total: '0.00', closing_balance: '0.00', planned_contributions_total: '0.00', entries: [], frozen: false, overdrawn: false, booking_basis: 'CASH' });
    if (url === "/api/properties") return json([{ id: "property-1", name: "Synthetic house" }]);
    if (url.startsWith("/api/bank-accounts")) return json([{ id: "bank-1", label: "Giro", account_type: "LAUFEND" }]);
    if (init?.method === "POST") {
      const body = JSON.parse(init.body as string); bodies.push(body);
      return url.endsWith("/compute") ? json({ detail: { code: "financial_inputs_incomplete" } }, 422) : json({ id: "coverage-1" }, 201);
    }
    return json({ id: "run-1", status: "draft", property_id: "property-1", period_end: "2025-12-31", engine_version: "test", lines: [] });
  }));
  render(<MemoryRouter initialEntries={["/report/run-1"]}><Routes><Route path="/report/:runId" element={<AssetReportDetailPage />} /></Routes></MemoryRouter>);
  const opening = await screen.findByLabelText("Rücklage Anfangsbestand");
  expect(screen.getByLabelText('Begründung der Rücklagenwerte').closest('.form-grid')).not.toBeNull();
  const bank = screen.getByLabelText("Kontostand · Giro");
  expect(opening).toHaveValue(""); expect(bank).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await waitFor(() => expect(bodies).toHaveLength(1));
  expect(bodies[0]).toMatchObject({ reserve_opening_balance: null, bank_balances: [{ balance: "" }] });
  for (const input of [opening, bank]) {
    fireEvent.change(input, { target: { value: "0.00" } });
  }
  fireEvent.change(screen.getByLabelText("Grundlage der Prüfung"), { target: { value: "Complete records, no movements" } });
  fireEvent.change(screen.getByLabelText('Begründung der Rücklagenwerte'), { target: { value: 'Geprüfter Nullbestand' } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Datengrundlage speichern" }));
  await screen.findByRole("status");
  expect(bodies[1]).toMatchObject({ values: { reserve_opening_balance: "0.00", bank_balances: [{ balance: "0.00" }], receivables: [], payables: [] }, confirmed_complete: true });
  expect(bodies[1].values).toEqual({
    reserve_opening_balance: "0.00", reserve_override_reason: "Geprüfter Nullbestand",
    bank_balances: [{ bank_account_id: "bank-1", label: "Giro", account_type: "LAUFEND", balance: "0.00" }],
    receivables: [], payables: [],
  });
});


it('does not offer editable derived reserve totals', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => json(url.endsWith('/reserve-ledger')
    ? { opening_balance: '0.00', contributions_total: '0.00', withdrawals_total: '0.00', overrides_total: '0.00', closing_balance: '0.00', planned_contributions_total: '0.00', entries: [], frozen: false, booking_basis: 'CASH' }
    : url === '/api/properties' || url.includes('/bank-accounts') || url.includes('/persons') || url.includes('/owner-profiles') ? []
    : { id: 'run-1', property_id: 'property-1', status: 'draft', lines: [], engine_version: 'test' })));
  render(<MemoryRouter initialEntries={['/report/run-1']}><Routes><Route path="/report/:runId" element={<AssetReportDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByLabelText('Rücklage Anfangsbestand');
  expect(screen.queryByLabelText('Rücklage Zuführung')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Rücklage Entnahmen')).not.toBeInTheDocument();
});

it('refetches the computation table after a reserve correction', async () => {
  let corrected = false;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') { corrected = true; return json({ id: 'correction' }, 201); }
    if (url.endsWith('/reserve-ledger')) return json({ opening_balance: '0.00', contributions_total: '0.00', withdrawals_total: '0.00', overrides_total: '0.00', closing_balance: '0.00', planned_contributions_total: '0.00', entries: [], frozen: false, booking_basis: 'CASH' });
    if (url === '/api/properties' || url.includes('/bank-accounts')) return json([]);
    return json({ id: 'run-1', property_id: 'property-1', status: 'draft', engine_version: 'test', lines: corrected ? [] : [
      { id: 'line', line_type: 'RESERVE_OPENING', label: 'Vorheriger Anfangsbestand', amount: '0.00' },
    ] });
  });
  vi.stubGlobal('fetch', fetchMock);
  render(<MemoryRouter initialEntries={['/report/run-1']}><Routes><Route path="/report/:runId" element={<AssetReportDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByRole('heading', { name: 'Vermögensaufstellung' });
  // The panel renders after the ledger loads; wait instead of reading it synchronously.
  fireEvent.click(await screen.findByRole('button', { name: 'Korrektur erfassen' }));
  fireEvent.change(screen.getByLabelText('Art'), { target: { value: 'OPENING' } });
  fireEvent.change(screen.getByLabelText(/^(Betrag|Korrigierter Anfangsbestand)/), { target: { value: '500,00' } });
  fireEvent.change(screen.getByLabelText('Begründung *'), { target: { value: 'Bankunterlagen geprüft' } });
  fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
  await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/weg-settlements/run-1')).toHaveLength(2));
  expect(screen.queryByRole('heading', { name: 'Vermögensaufstellung' })).not.toBeInTheDocument();
});
