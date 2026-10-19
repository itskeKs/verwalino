/**
 * Tests for SettlementDetailPage.tsx — validates that BK-Zusatzeingaben
 * (needsConsumption, needsPersonCount, needsExternalInput, needsManualInput)
 * are correctly detected from cost entries with DB values.
 *
 * T03: "Wasser nach Verbrauch, Heizung extern eingeben, berechnen, finalisieren, PDF öffnen".
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import * as api from "../api/endpoints";
import type { CostEntry, SettlementRun } from "../api/types";

vi.mock("../api/endpoints", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/endpoints")>(),
  getSettlement: vi.fn(),
  listProperties: vi.fn().mockResolvedValue([]),
  listLeases: vi.fn().mockResolvedValue([]),
  listUnits: vi.fn().mockResolvedValue([]),
  listTenantProfiles: vi.fn().mockResolvedValue([]),
  listPersons: vi.fn().mockResolvedValue([]),
  listCostCategories: vi.fn().mockResolvedValue([]),
  listCostEntries: vi.fn(),
  computeSettlement: vi.fn(),
  finalizeSettlement: vi.fn(),
}));
vi.mock("../components/FinancialCoverageReview", () => ({
  FinancialCoverageReview: () => null,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
import {
  SettlementDetailPage,
  needsConsumption,
  needsPersonCount,
  needsExternalInput,
  needsManualInput,
} from "./SettlementDetailPage";

// Helper to create a minimal CostEntry for testing
function createCostEntry(overrides: Partial<CostEntry> = {}): CostEntry {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    organization_id: "org-1", property_id: "property-1", management_mandate_id: null,
    cost_category_id: "category-1", cost_category_plan_version: 1, vendor_id: null,
    invoice_date: "2025-12-31", service_period_from: "2025-01-01", service_period_to: "2025-12-31",
    gross_amount: "600.00", vat_amount: null, vat_rate: null, document_number: null,
    owner_distribution_key: null, tenant_apportionable: "UMLAGEFAEHIG",
    apportionable_tenant_amount: null, status: "confirmed", source: "MANUAL",
    confirmed_at: "2026-01-01", confirmed_by: "user-1", superseded_by_id: null,
    tenant_distribution_key: "",
    ...overrides,
  };
}

describe("SettlementDetailPage with database distribution keys", () => {
  it.each([
    ["VERBRAUCH", true, false, false, false],
    ["PERSONENZAHL", false, true, false, false],
    ["EXTERNE_ABRECHNUNG", false, false, true, false],
    ["MANUELLER_BETRAG", false, false, false, true],
    ["WOHNFLAECHE", false, false, false, false],
  ])("recognizes %s", (key, consumption, persons, external, manual) => {
    const entry = createCostEntry({ tenant_distribution_key: key });
    expect(needsConsumption([entry])).toBe(consumption);
    expect(needsPersonCount([entry])).toBe(persons);
    expect(needsExternalInput(entry)).toBe(external);
    expect(needsManualInput(entry)).toBe(manual);
  });

  it("sends structured database-key inputs when computing and finalizing", async () => {
    const run: SettlementRun = {
      id: "run-1", organization_id: "org-1", property_id: "property-1", unit_id: "unit-1",
      management_mandate_id: null, lease_agreement_id: "lease-1", tenant_profile_id: "tenant-1",
      run_type: "BK_MIETER", period_start: "2025-01-01", period_end: "2025-12-31",
      status: "draft", advance_payments_total: "0.00", advance_payments_source: "MANUAL",
      total_cost: null, balance: null, snapshot_sha256: null, engine_version: "m3.1.0",
      finalized_at: null, finalized_by: null, superseded_by_id: null, lines: [],
    };
    vi.mocked(api.getSettlement).mockResolvedValue(run);
    vi.mocked(api.listCostEntries).mockResolvedValue([
      createCostEntry({ tenant_distribution_key: "VERBRAUCH", id: "water" }),
      createCostEntry({ tenant_distribution_key: "EXTERNE_ABRECHNUNG", id: "heating" }),
      createCostEntry({ tenant_distribution_key: "MANUELLER_BETRAG", id: "manual" }),
      createCostEntry({ tenant_distribution_key: "PERSONENZAHL", id: "persons" }),
    ]);
    vi.mocked(api.computeSettlement).mockResolvedValue(run);
    vi.mocked(api.finalizeSettlement).mockResolvedValue({ ...run, status: "finalized" });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const openPdf = vi.spyOn(window, "open").mockReturnValue(null);
    const user = userEvent.setup();
    render(<MemoryRouter initialEntries={["/abrechnungen/run-1"]}>
      <Routes><Route path="/abrechnungen/:runId" element={<SettlementDetailPage />} /></Routes>
    </MemoryRouter>);

    await user.click(await screen.findByRole("button", { name: "Strukturierte Eingaben anzeigen" }));
    await user.type(await screen.findByLabelText(/^Verbrauch \(/), "30");
    await user.type(screen.getByLabelText(/^Personenzahl \(/), "2");
    await user.type(screen.getByLabelText("heating"), "210.00");
    await user.type(screen.getByLabelText("manual"), "12.34");
    const expected = {
      external_amounts: [{ cost_entry_id: "heating", unit_id: "unit-1", tenant_amount: "210.00" }],
      manual_amounts: [{ cost_entry_id: "manual", unit_id: "unit-1", amount: "12.34" }],
      unit_metrics: [{ unit_id: "unit-1", consumption: "30", person_count: 2 }],
    };
    expect(screen.queryByRole("button", { name: "PDF herunterladen" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Berechnen" }));
    await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledWith("run-1", expected));
    await user.click(screen.getByRole("button", { name: "Finalisieren" }));
    await waitFor(() => expect(api.finalizeSettlement).toHaveBeenCalledWith("run-1", expected));
    await user.click(await screen.findByRole("button", { name: "PDF herunterladen" }));
    expect(openPdf).toHaveBeenCalledWith("/api/settlements/run-1/pdf", "_blank");
  });
});

describe("SettlementDetailPage needs* functions", () => {
  describe("needsConsumption", () => {
    it("returns true when entries contain CONSUMPTION", () => {
      const entries = [createCostEntry({ tenant_distribution_key: "CONSUMPTION" })];
      expect(needsConsumption(entries)).toBe(true);
    });

    it("returns false when no entries have CONSUMPTION", () => {
      const entries = [createCostEntry({ tenant_distribution_key: "MEA" })];
      expect(needsConsumption(entries)).toBe(false);
    });

    it("returns true if any entry has CONSUMPTION among many", () => {
      const entries = [
        createCostEntry({ tenant_distribution_key: "MEA" }),
        createCostEntry({ tenant_distribution_key: "CONSUMPTION" }),
        createCostEntry({ tenant_distribution_key: "PERSON_COUNT" }),
      ];
      expect(needsConsumption(entries)).toBe(true);
    });
  });

  describe("needsPersonCount", () => {
    it("returns true when entries contain PERSON_COUNT", () => {
      const entries = [createCostEntry({ tenant_distribution_key: "PERSON_COUNT" })];
      expect(needsPersonCount(entries)).toBe(true);
    });

    it("returns false when no entries have PERSON_COUNT", () => {
      const entries = [createCostEntry({ tenant_distribution_key: "MEA" })];
      expect(needsPersonCount(entries)).toBe(false);
    });
  });

  describe("needsExternalInput", () => {
    it("returns true when entry has EXTERNAL distribution key", () => {
      const entry = createCostEntry({ tenant_distribution_key: "EXTERNAL" });
      expect(needsExternalInput(entry)).toBe(true);
    });

    it("returns false when entry does not have EXTERNAL distribution key", () => {
      const entry = createCostEntry({ tenant_distribution_key: "MEA" });
      expect(needsExternalInput(entry)).toBe(false);
    });
  });

  describe("needsManualInput", () => {
    it("returns true when entry has MANUAL distribution key", () => {
      const entry = createCostEntry({ tenant_distribution_key: "MANUAL" });
      expect(needsManualInput(entry)).toBe(true);
    });

    it("returns false when entry does not have MANUAL distribution key", () => {
      const entry = createCostEntry({ tenant_distribution_key: "MEA" });
      expect(needsManualInput(entry)).toBe(false);
    });
  });
});
