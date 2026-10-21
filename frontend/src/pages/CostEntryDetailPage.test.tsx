import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CostEntryDetailPage } from "./CostEntryDetailPage";
import * as api from "../api/endpoints";
import type { CostEntry } from "../api/types";

vi.mock("../api/endpoints", () => ({ getCostEntry: vi.fn(), updateCostEntry: vi.fn(), confirmCostEntry: vi.fn(), rejectCostEntry: vi.fn(), createCostEntryCorrection: vi.fn(), listCostCategories: vi.fn(), listProperties: vi.fn(), listInvoiceDocuments: vi.fn() }));
const entry: CostEntry = {
  id: "cost", organization_id: "org", property_id: "property", management_mandate_id: null,
  cost_category_id: "water", cost_category_plan_version: 1, vendor_id: null,
  invoice_date: "2026-12-15", service_period_from: "2026-01-01", service_period_to: "2026-12-31",
  gross_amount: "100.00", vat_amount: "0.00", vat_rate: "0", document_number: "Synthetic water",
  owner_distribution_key: "WOHNFLAECHE", tenant_distribution_key: "WOHNFLAECHE",
  tenant_apportionable: "UMLAGEFAEHIG", apportionable_tenant_amount: null,
  status: "needs_review", source: "MANUAL", confirmed_at: null, confirmed_by: null, superseded_by_id: null,
};
afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getCostEntry).mockResolvedValue(structuredClone(entry));
  vi.mocked(api.listCostCategories).mockResolvedValue([]);
  vi.mocked(api.listProperties).mockResolvedValue([]);
  vi.mocked(api.listInvoiceDocuments).mockResolvedValue([]);
  vi.mocked(api.updateCostEntry).mockResolvedValue({ ...entry, tenant_distribution_key: "VERBRAUCH" });
  vi.mocked(api.createCostEntryCorrection).mockResolvedValue({ ...entry, id: "new-cost" });
});
function show() {
  render(<MemoryRouter initialEntries={["/kosten/cost"]}><Routes>
    <Route path="/kosten/:entryId" element={<CostEntryDetailPage />} />
  </Routes></MemoryRouter>);
}
const expectedBody = {
  gross_amount: "100.00", vat_amount: "0.00", vat_rate: "0", invoice_date: "2026-12-15",
  service_period_from: "2026-01-01", service_period_to: "2026-12-31", cost_category_id: "water",
  vendor_id: null, document_number: "Synthetic water", owner_distribution_key: "WOHNFLAECHE",
  tenant_distribution_key: "VERBRAUCH", tenant_apportionable: "UMLAGEFAEHIG", apportionable_tenant_amount: null,
};
it.each(["edit", "correction"])("exposes exact native labels and canonical preselection in %s", async mode => {
  vi.mocked(api.getCostEntry).mockResolvedValue({ ...entry, status: mode === "edit" ? "needs_review" : "confirmed", tenant_distribution_key: "VERBRAUCH" });
  show(); fireEvent.click(await screen.findByRole("button", { name: mode === "edit" ? "Bearbeiten" : "Korrektur erstellen" }));
  expect(screen.getByLabelText("Verteilerschl. Mieter", { exact: true })).toHaveValue("CONSUMPTION");
  expect(screen.getByLabelText("Verteilerschl. Eigentuemer", { exact: true })).toHaveValue("AREA");
  expect(screen.getByLabelText("Umlagefaehigkeit", { exact: true })).toHaveValue("full");
});
it.each(["edit", "correction"])("sends only canonical database vocabulary through %s", async mode => {
  vi.mocked(api.getCostEntry).mockResolvedValue({ ...entry, status: mode === "edit" ? "needs_review" : "confirmed" });
  show(); fireEvent.click(await screen.findByRole("button", { name: mode === "edit" ? "Bearbeiten" : "Korrektur erstellen" }));
  fireEvent.change(screen.getByLabelText("Verteilerschl. Mieter", { exact: true }), { target: { value: "CONSUMPTION" } });
  fireEvent.change(screen.getByLabelText("Verteilerschl. Eigentuemer", { exact: true }), { target: { value: "AREA" } });
  fireEvent.change(screen.getByLabelText("Umlagefaehigkeit", { exact: true }), { target: { value: "full" } });
  const submit = screen.getByRole("button", { name: mode === "edit" ? /^Speichern$/ : /^Korrektur erstellen$/ });
  fireEvent.click(submit);
  const mutate = mode === "edit" ? api.updateCostEntry : api.createCostEntryCorrection;
  await waitFor(() => expect(mutate).toHaveBeenCalledWith("cost", expectedBody));
  if (mode === "correction") expect(api.updateCostEntry).not.toHaveBeenCalled();
});
