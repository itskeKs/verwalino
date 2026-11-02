import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AssetReportDetailPage } from "./AssetReportDetailPage";
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

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
