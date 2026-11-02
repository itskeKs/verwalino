/**
 * Tests for SettlementDetailPage.tsx — validates that BK-Zusatzeingaben
 * (needsConsumption, needsPersonCount, needsExternalInput, needsManualInput)
 * are correctly detected from cost entries with DB values.
 *
 * T03: "Wasser nach Verbrauch, Heizung extern eingeben, berechnen, finalisieren, PDF öffnen".
 */

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AuthCtx } from "../auth/useAuth";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import * as api from "../api/endpoints";
import type { ConsumptionSuggestion, CostEntry, SettlementRun } from "../api/types";
import { ApiError } from "../api/client";

vi.mock("../api/endpoints", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/endpoints")>(),
  getSettlement: vi.fn(),
  getConsumptionSuggestions: vi.fn().mockResolvedValue([{ unit_id: "unit-1", unit_label: "1", consumption: null, meter_id: null, start_reading: null, end_reading: null, reason: "Handeingabe erforderlich." }]),
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
vi.mock("../api/mail", () => ({ getMailDispatch: vi.fn().mockResolvedValue({ recipients: [], attempts: [] }), sendMailDispatch: vi.fn() }));
vi.mock("../components/FinancialCoverageReview", () => ({
  FinancialCoverageReview: () => null,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
import { SettlementDetailPage } from "./SettlementDetailPage";
import {
  needsConsumption,
  needsPersonCount,
  needsExternalInput,
  needsManualInput,
} from "../lib/settlementInputNeeds";

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
      status: "draft", advance_payments_total: "0.00", advance_payments_source: "MANUAL", heating_advance_payments_total: "0.00", heating_advance_payments_source: "MANUAL",
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
    render(<AuthCtx.Provider value={auth("OWNER_ADMIN")}><MemoryRouter initialEntries={["/abrechnungen/run-1"]}>
      <Routes><Route path="/abrechnungen/:runId" element={<SettlementDetailPage />} /></Routes>
    </MemoryRouter></AuthCtx.Provider>);

    await user.click(await screen.findByRole("button", { name: "Strukturierte Eingaben anzeigen" }));
    await user.type(await screen.findByLabelText(/^Verbrauch \(/), "30");
    await user.type(screen.getByLabelText(/^Personenzahl \(/), "2");
    await user.type(screen.getByLabelText("heating"), "210.00");
    await user.type(screen.getByLabelText("manual"), "12.34");
    const expected = {
      heating_inputs: [],
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

const finalized: SettlementRun = {
  id: "run-1", organization_id: "org", property_id: "property-1", unit_id: "unit-1",
  management_mandate_id: null, lease_agreement_id: "lease-1", tenant_profile_id: "tenant-1",
  run_type: "BK_MIETER", period_start: "2026-01-01", period_end: "2026-12-31",
  status: "finalized", advance_payments_total: "1800.00", advance_payments_source: "MANUAL", heating_advance_payments_total: "0.00", heating_advance_payments_source: "MANUAL",
  total_cost: "1200.00", balance: "-600.00", snapshot_sha256: "original-snapshot",
  engine_version: "m3.1.0", finalized_at: "2027-01-01", finalized_by: "user",
  superseded_by_id: null, lines: [],
};
const proposal = {
  lease_agreement_id: "lease-1", tenant_cost: "1200.00", months: "12",
  monthly_amount: "100.00", heating_monthly_amount: "0.00", heating_tenant_cost: "0.00", period_start: "2026-01-01", period_end: "2026-12-31",
};
const originalLease = {
  id: "lease-1", unit_id: "unit-1", tenant_profile_id: "tenant-1", valid_from: "2025-01-01",
  valid_to: null, base_rent: "700.00", advance_payment: "150.00", heating_advance_payment: "0.00", deposit: null, notes: "Original",
};
const applied = {
  id: "change-1", lease_agreement_id: "lease-1", valid_from: "2027-01-01",
  base_rent: "725.00", advance_payment: "100.00", heating_advance_payment: "0.00", created_at: "2026-10-20T12:00:00Z",
};
function auth(role: string) {
  return { loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() };
}
function showProposal(role = "OWNER_ADMIN") {
  return render(<AuthCtx.Provider value={auth(role)}><MemoryRouter initialEntries={["/abrechnungen/run-1"]}>
    <Link to="/abrechnungen/run-2">Andere Abrechnung</Link>
    <Routes><Route path="/abrechnungen/:runId" element={<SettlementDetailPage />} /></Routes>
  </MemoryRouter></AuthCtx.Provider>);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
let fetchProposal: ReturnType<typeof vi.fn>;
function posts() { return fetchProposal.mock.calls.filter(([, options]) => options?.method === "POST"); }
function date(value: string) { fireEvent.change(screen.getByLabelText("Übernehmen ab", { exact: true }), { target: { value } }); }
function accept() { fireEvent.click(screen.getByRole("button", { name: "Vorauszahlungen übernehmen" })); }

describe("T28 monthly advance proposal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettlement).mockReset().mockResolvedValue(structuredClone(finalized));
    vi.mocked(api.listCostEntries).mockResolvedValue([]);
    vi.mocked(api.listLeases).mockResolvedValue([originalLease]);
    fetchProposal = vi.fn(async (_url: string, options?: RequestInit) => new Response(
      JSON.stringify(options?.method === "POST" ? applied : proposal), { status: options?.method === "POST" ? 201 : 200 },
    ));
    vi.stubGlobal("fetch", fetchProposal);
  });

  it("provides helpers with exact URL and date-only payload", async () => {
    expect(await api.getAdvanceProposal("run-1")).toEqual(proposal);
    const dateOnly = { valid_from: "2027-01-01", base_rent: "1.00", advance_payment: "999.00", heating_advance_payment: "0.00" };
    expect(await api.applyAdvanceProposal("run-1", dateOnly)).toEqual(applied);
    expect(fetchProposal.mock.calls.map(([url]) => url)).toEqual([
      "/api/settlements/run-1/advance-proposal", "/api/settlements/run-1/advance-proposal",
    ]);
    expect(JSON.parse(posts()[0][1].body)).toEqual({ valid_from: "2027-01-01" });
  });

  it.each([
    ["legacy heating costs", "Geleistete Vorauszahlungen", "83.33", "1000.00"],
    ["nonzero heating basis rounded to zero", "Geleistete Vorauszahlungen", "0.00", "0.01"],
    ["nonzero heating advance", "Geleistete Vorauszahlungen", "83.33", "0.00"],
    ["Bernd without heating", "Geleistete Vorauszahlungen", "0.00", "0.00"],
    ["split statement without heating proposal", "Betriebskosten-Vorauszahlungen", "0.00", "0.00"],
  ])("shows the proposal payload and every accepted value for %s", async (_case, label, heatingAmount, heatingBasis) => {
    vi.mocked(api.getSettlement).mockResolvedValue({ ...finalized, lines: [{
      id: "advance-1", cost_entry_id: null, line_type: "ADVANCE_PAYMENT", cost_category_code: null,
      label,
      distribution_key: null, total_amount: null, distribution_base_total: null, distribution_base_share: null,
      time_share_days: null, time_share_total_days: null, amount: "1800.00", sort_order: 1, note: null,
    }] });
    fetchProposal.mockImplementation(async (_url: string, options?: RequestInit) => new Response(JSON.stringify(
      options?.method === "POST" ? { ...applied, advance_payment: "158.33", heating_advance_payment: heatingAmount }
        : { ...proposal, monthly_amount: "158.33", tenant_cost: "1900.00",
          heating_monthly_amount: heatingAmount, heating_tenant_cost: heatingBasis },
    ), { status: options?.method === "POST" ? 201 : 200 }));
    showProposal();
    const region = await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" });
    await within(region).findByText("158,33 EUR");
    expect(region).toHaveTextContent("Betriebskosten-Vorauszahlung: 158,33 EUR monatlich");
    expect(region).toHaveTextContent("Grundlage Betriebskosten: 1.900,00 EUR Mieteranteil / 12 Monate");
    if (heatingAmount === "0.00" && heatingBasis === "0.00") {
      expect(region).not.toHaveTextContent("Heizkosten-Vorauszahlung");
      expect(region).not.toHaveTextContent("Grundlage Heizkosten");
      expect(region).toHaveTextContent("Nur die Vorauszahlung wird geändert.");
    } else {
      expect(region).toHaveTextContent(`Heizkosten-Vorauszahlung: ${heatingAmount.replace(".", ",")} EUR monatlich`);
      const formattedBasis = heatingBasis === "1000.00" ? "1.000,00" : heatingBasis.replace(".", ",");
      expect(region).toHaveTextContent(`Grundlage Heizkosten: ${formattedBasis} EUR Mieteranteil / 12 Monate`);
      expect(region).toHaveTextContent("Beide Vorauszahlungen werden geändert.");
    }
    accept();
    const status = await within(region).findByRole("status");
    expect(status).toHaveTextContent("Miethistorie ergänzt");
    expect(status).toHaveTextContent("Kaltmiete 725,00 EUR");
    expect(status).toHaveTextContent("Betriebskosten-Vorauszahlung 158,33 EUR");
    expect(status).toHaveTextContent(`Heizkosten-Vorauszahlung ${heatingAmount.replace(".", ",")} EUR`);
    expect(posts()).toHaveLength(1);
    expect(JSON.parse(posts()[0][1].body)).toEqual({ valid_from: "2027-01-01" });
  });

  it.each(["OWNER_ADMIN", "CLERK"])("offers basis, date and acceptance to %s without changing lease or snapshot", async (role) => {
    const leaseBefore = structuredClone(originalLease);
    showProposal(role);
    const region = await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" });
    expect(await within(region).findByText("100,00 EUR")).toBeVisible();
    expect(region).toHaveTextContent("Betriebskosten-Vorauszahlung");
    expect(region).toHaveTextContent("1.200,00 EUR");
    expect(region).toHaveTextContent("12 Monate");
    expect(region).toHaveTextContent(/Kaltmiete.*gewählten Tag/);
    expect(screen.getByLabelText("Übernehmen ab", { exact: true })).toHaveValue("2027-01-01");
    accept();
    expect(await screen.findByText(/Miethistorie ergänzt/)).toBeVisible();
    expect(region).toHaveTextContent("725,00 EUR");
    expect(region).toHaveTextContent("2027-01-01");
    expect(JSON.parse(posts()[0][1].body)).toEqual({ valid_from: "2027-01-01" });
    expect(screen.getByRole("button", { name: "PDF herunterladen" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Korrekturabrechnung erstellen" })).toBeEnabled();
    expect(screen.getByText("original-snapshot")).toBeVisible();
    expect(screen.getByText("1800.00 EUR")).toBeVisible();
    expect(api.getSettlement).toHaveBeenCalledTimes(1);
    expect(api.computeSettlement).not.toHaveBeenCalled();
    expect(api.finalizeSettlement).not.toHaveBeenCalled();
    expect(originalLease).toEqual(leaseBefore);
  });

  it.each(["READ_ONLY", "UNKNOWN"])("shows proposal without acceptance controls for %s", async (role) => {
    showProposal(role); expect(await screen.findByText("100,00 EUR")).toBeVisible();
    expect(screen.queryByLabelText("Übernehmen ab", { exact: true })).toBeNull();
    expect(screen.queryByRole("button", { name: "Vorauszahlungen übernehmen" })).toBeNull();
    expect(posts()).toHaveLength(0);
  });

  it.each([{ status: "draft" as const }, { superseded_by_id: "correction" }, { run_type: "OTHER" }])("never fetches/offers an ineligible run: %j", async (overrides) => {
    vi.mocked(api.getSettlement).mockResolvedValue({ ...finalized, ...overrides });
    showProposal(); await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("region", { name: "Vorauszahlungsvorschlag" })).toBeNull();
    expect(fetchProposal).not.toHaveBeenCalled();
  });

  it("shows GET loading, visible failure and retry, not a zero proposal", async () => {
    const pending = deferred<Response>(); fetchProposal.mockReturnValueOnce(pending.promise);
    showProposal(); const region = await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" });
    expect(region).toHaveTextContent(/Lade.*Vorauszahlung/);
    await act(async () => pending.resolve(new Response(JSON.stringify({ detail: "Vorschlag nicht verfügbar" }), { status: 409 })));
    expect(await within(region).findByRole("alert")).toHaveTextContent("Vorschlag nicht verfügbar");
    expect(region).not.toHaveTextContent("0,00 EUR");
    fireEvent.click(within(region).getByRole("button", { name: "Erneut versuchen" }));
    expect(await within(region).findByText("100,00 EUR")).toBeVisible();
  });

  it.each([403, 404, 409, 422, "network"])("retains proposal/date after POST %s and allows retry", async (failure) => {
    showProposal(); await screen.findByText("100,00 EUR"); date("2026-03-15");
    if (failure === "network") fetchProposal.mockRejectedValueOnce(new TypeError("Netzwerk nicht erreichbar"));
    else fetchProposal.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Serverprüfung fehlgeschlagen" }), { status: Number(failure) }));
    accept();
    expect(await screen.findByRole("alert")).toHaveTextContent(failure === "network" ? "Netzwerk nicht erreichbar" : "Serverprüfung fehlgeschlagen");
    expect(screen.getByLabelText("Übernehmen ab", { exact: true })).toHaveValue("2026-03-15");
    expect(screen.getByText("100,00 EUR")).toBeVisible();
    await waitFor(() => expect(screen.getByRole("button", { name: "Vorauszahlungen übernehmen" })).toBeEnabled());
    accept(); await screen.findByText(/Miethistorie ergänzt/);
    expect(JSON.parse(posts()[1][1].body)).toEqual({ valid_from: "2026-03-15" });
  });

  it("rejects empty dates locally but permits historical dates", async () => {
    showProposal(); await screen.findByText("100,00 EUR"); date(""); accept();
    expect(await screen.findByRole("alert")).toHaveTextContent(/gültiges Datum/);
    expect(posts()).toHaveLength(0);
    date("2025-02-28"); accept(); await screen.findByText(/Miethistorie ergänzt/);
    expect(JSON.parse(posts()[0][1].body)).toEqual({ valid_from: "2025-02-28" });
  });

  it.each(["2026-02-30", "0000-01-01", "10000-01-01"])("rejects an invalid or unrepresentable acceptance date %s", async (value) => {
    showProposal(); await screen.findByText("100,00 EUR"); date(value); accept();
    expect(await screen.findByRole("alert")).toHaveTextContent(/gültiges Datum/);
    expect(posts()).toHaveLength(0);
  });

  it("shows network GET errors and retries without inventing amounts", async () => {
    fetchProposal.mockRejectedValueOnce(new TypeError("Verbindung unterbrochen"));
    showProposal();
    const region = await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" });
    expect(await within(region).findByRole("alert")).toHaveTextContent("Verbindung unterbrochen");
    expect(region).not.toHaveTextContent("0,00 EUR");
    expect(screen.queryByLabelText("Übernehmen ab")).toBeNull();
    fireEvent.click(within(region).getByRole("button", { name: "Erneut versuchen" }));
    expect(await within(region).findByText("100,00 EUR")).toBeVisible();
  });

  it("does not turn an unknown financial amount into zero", async () => {
    fetchProposal.mockResolvedValueOnce(new Response(JSON.stringify({ ...proposal, monthly_amount: "", tenant_cost: "", heating_monthly_amount: "", heating_tenant_cost: "" })));
    showProposal();
    const region = await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" });
    expect(await within(region).findAllByText("Nicht verfügbar")).toHaveLength(2);
    expect(region).not.toHaveTextContent("0,00 EUR");
  });

  it("disables date/submit and synchronously prevents double submissions", async () => {
    const pending = deferred<Response>();
    showProposal(); await screen.findByText("100,00 EUR"); fetchProposal.mockReturnValueOnce(pending.promise);
    const button = screen.getByRole("button", { name: "Vorauszahlungen übernehmen" });
    const form = button.closest("form")!;
    act(() => { fireEvent.submit(form); fireEvent.submit(form); });
    expect(posts()).toHaveLength(1); expect(button).toBeDisabled();
    expect(screen.getByLabelText("Übernehmen ab", { exact: true })).toBeDisabled();
    await act(async () => pending.resolve(new Response(JSON.stringify(applied), { status: 201 })));
    expect(await screen.findByText(/Miethistorie ergänzt/)).toBeVisible();
  });

  it("formats large decimal strings without losing cents", async () => {
    fetchProposal.mockResolvedValueOnce(new Response(JSON.stringify({ ...proposal, monthly_amount: "9007199254740993.01" })));
    showProposal(); expect(await screen.findByText("9.007.199.254.740.993,01 EUR")).toBeVisible();
  });

  it.each([["2026-02-28", "2026-03-01"], ["2024-02-28", "2024-02-29"], ["9999-12-31", ""]])("defaults next day after %s when representable", async (end, expected) => {
    fetchProposal.mockResolvedValueOnce(new Response(JSON.stringify({ ...proposal, period_end: end })));
    showProposal(); await screen.findByText("100,00 EUR");
    expect(screen.getByLabelText("Übernehmen ab", { exact: true })).toHaveValue(expected);
  });

  it("ignores late GET after navigation to a draft", async () => {
    const pending = deferred<Response>(); fetchProposal.mockReturnValueOnce(pending.promise);
    vi.mocked(api.getSettlement).mockResolvedValueOnce(finalized).mockResolvedValueOnce({ ...finalized, id: "run-2", status: "draft" });
    showProposal(); await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" });
    fireEvent.click(screen.getByRole("link", { name: "Andere Abrechnung" }));
    await screen.findByRole("button", { name: "Finalisieren" });
    await act(async () => pending.resolve(new Response(JSON.stringify(proposal))));
    expect(screen.queryByRole("region", { name: "Vorauszahlungsvorschlag" })).toBeNull();
  });

  it("ignores late POST on another finalized run", async () => {
    const pending = deferred<Response>();
    vi.mocked(api.getSettlement).mockResolvedValueOnce(finalized).mockResolvedValueOnce({ ...finalized, id: "run-2" });
    showProposal(); await screen.findByText("100,00 EUR"); fetchProposal.mockReturnValueOnce(pending.promise); accept();
    fireEvent.click(screen.getByRole("link", { name: "Andere Abrechnung" }));
    await waitFor(() => expect(fetchProposal).toHaveBeenCalledWith("/api/settlements/run-2/advance-proposal", expect.anything()));
    await screen.findByText("100,00 EUR");
    await act(async () => pending.resolve(new Response(JSON.stringify(applied), { status: 201 })));
    expect(screen.queryByText(/Miethistorie ergänzt/)).toBeNull();
    expect(screen.getByLabelText("Übernehmen ab", { exact: true })).toBeEnabled();
  });

  it("ignores an obsolete initial settlement response before fetching proposals", async () => {
    const pending = deferred<SettlementRun>();
    vi.mocked(api.getSettlement).mockReturnValueOnce(pending.promise).mockResolvedValueOnce({ ...finalized, id: "run-2", status: "draft" });
    showProposal();
    fireEvent.click(screen.getByRole("link", { name: "Andere Abrechnung" }));
    await screen.findByRole("button", { name: "Finalisieren" });
    await act(async () => pending.resolve(finalized));
    expect(screen.queryByRole("region", { name: "Vorauszahlungsvorschlag" })).toBeNull();
    expect(fetchProposal).not.toHaveBeenCalled();
  });

  it("ignores GET completion after unmount", async () => {
    const pending = deferred<Response>(); fetchProposal.mockReturnValueOnce(pending.promise);
    showProposal(); await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" }); cleanup();
    await act(async () => pending.reject(new TypeError("Late GET failure")));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("ignores completion after unmount", async () => {
    const pending = deferred<Response>(); showProposal(); await screen.findByText("100,00 EUR");
    fetchProposal.mockReturnValueOnce(pending.promise); accept(); cleanup();
    await act(async () => pending.reject(new TypeError("Late network failure")));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

const consumptionRows: ConsumptionSuggestion[] = [
  ["unit-1", "1", "30.0000", "130.0000"], ["unit-2", "2", "70.0000", "170.0000"],
].map(([unit_id, unit_label, consumption, end]) => ({
  unit_id, unit_label, consumption, meter_id: `meter-${unit_id}`, reason: null,
  start_reading: { id: `start-${unit_id}`, reading_date: "2025-01-01", value: "100.0000" },
  end_reading: { id: `end-${unit_id}`, reading_date: "2025-12-31", value: end },
}));
const consumptionDraft: SettlementRun = { ...finalized, status: "draft", snapshot_sha256: null,
  total_cost: null, balance: null, advance_payments_total: "0.00", finalized_at: null, finalized_by: null,
  period_start: "2025-01-01", period_end: "2025-12-31" };
async function showConsumption() {
  showProposal();
  fireEvent.click(await screen.findByRole("button", { name: "Strukturierte Eingaben anzeigen" }));
  return screen.findByLabelText(/^Verbrauch \(1/);
}
describe("T31 meter consumption prefill", () => {
  it('renders both component results and the overall balance in the existing EUR format', async () => {
    const labels = ['Betriebskosten abzüglich Vorauszahlungen', 'Heizkosten abzüglich Vorauszahlungen'];
    vi.mocked(api.getSettlement).mockResolvedValueOnce({ ...consumptionDraft, total_cost: '2900.00', balance: '20.00',
      advance_payments_total: '1800.00', heating_advance_payments_total: '1080.00',
      lines: labels.map((label, index) => ({ id: `partial-${index}`, cost_entry_id: null, line_type: 'SUBTOTAL',
        label, amount: index ? '-80.00' : '100.00', sort_order: index, cost_category_code: null,
        distribution_key: null, total_amount: null, distribution_base_total: null, distribution_base_share: null,
        time_share_days: null, time_share_total_days: null, note: 'Quelle: DERIVED' })) });
    showProposal();
    expect((await screen.findByText(labels[0])).closest('tr')).toHaveTextContent('100.00 EUR');
    expect(screen.getByText(labels[1]).closest('tr')).toHaveTextContent('-80.00 EUR');
    expect(screen.getByText('1080.00 EUR')).toBeVisible();
    expect(screen.getByText('20.00 EUR')).toBeVisible();
  });

  it('keeps Bernd on one advance block without heating costs or advances', async () => {
    vi.mocked(api.getSettlement).mockResolvedValueOnce({ ...consumptionDraft,
      advance_payments_total: '1440.00', heating_advance_payments_total: '0.00', total_cost: '970.00', balance: '-470.00',
      lines: [{ id: 'advance', cost_entry_id: null, line_type: 'ADVANCE_PAYMENT',
        label: 'Geleistete Vorauszahlungen', amount: '1440.00', sort_order: 0,
        cost_category_code: null, distribution_key: null, total_amount: null,
        distribution_base_total: null, distribution_base_share: null,
        time_share_days: null, time_share_total_days: null, note: 'Quelle: MANUAL' }] });
    vi.mocked(api.listCostEntries).mockResolvedValueOnce([]);
    showProposal();
    expect(await screen.findAllByText('Geleistete Vorauszahlungen')).toHaveLength(2);
    expect(screen.queryByText('Heizkosten-Vorauszahlungen')).toBeNull();
    expect(screen.queryByText('Betriebskosten abzüglich Vorauszahlungen')).toBeNull();
  });

  it('changes only the heating total and preserves the operating derived source', async () => {
    const run = { ...consumptionDraft, advance_payments_total: '1800.00', advance_payments_source: 'DERIVED',
      heating_advance_payments_total: '1080.00', heating_advance_payments_source: 'DERIVED' };
    vi.mocked(api.getSettlement).mockResolvedValueOnce(run);
    const save = vi.spyOn(api, 'setAdvancePayments').mockResolvedValue({ ...run,
      heating_advance_payments_total: '1000.00', heating_advance_payments_source: 'MANUAL' });
    showProposal();
    fireEvent.click(await screen.findByRole('button', { name: 'Aendern' }));
    fireEvent.change(screen.getByLabelText('Heizkosten-Vorauszahlung (EUR)'), { target: { value: '1000,00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
    await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith('run-1', { heating_advance_payments_total: '1000.00' }));
    expect(screen.getByText('Betriebskosten-Vorauszahlungen').closest('tr')).toHaveTextContent('(DERIVED)');
    expect(screen.getByText('Heizkosten-Vorauszahlungen').closest('tr')).toHaveTextContent('(MANUAL)');
  });

  it('keeps an unchanged unknown operating total when entering heating advances', async () => {
    const run = { ...consumptionDraft, advance_payments_total: null };
    vi.mocked(api.getSettlement).mockResolvedValueOnce(run);
    const save = vi.spyOn(api, 'setAdvancePayments').mockResolvedValue({ ...run, heating_advance_payments_total: '90.00' });
    showProposal();
    fireEvent.click(await screen.findByRole('button', { name: 'Aendern' }));
    fireEvent.change(screen.getByLabelText('Heizkosten-Vorauszahlung (EUR)'), { target: { value: '90.00' } });
    fireEvent.click(screen.getByRole('button', { name: 'Speichern' }));
    await waitFor(() => expect(save).toHaveBeenCalledExactlyOnceWith('run-1', { heating_advance_payments_total: '90.00' }));
  });

  it("shows excluded payments as a notice without a second financial amount", async () => {
    const notice = "Nicht angerechnet: Zahlungen außerhalb des Mietverhältnisses: 1.234,56 €";
    vi.mocked(api.getSettlement).mockResolvedValueOnce({ ...consumptionDraft, lines: [{
      id: "notice", cost_entry_id: null, line_type: "SUBTOTAL", label: notice, amount: "0.00", sort_order: 0,
      cost_category_code: null, distribution_key: null, total_amount: null,
      distribution_base_total: null, distribution_base_share: null,
      time_share_days: null, time_share_total_days: null, note: null,
    }] });
    await showConsumption();
    const row = (await screen.findByText(notice)).closest("tr")!;
    expect(row).toHaveTextContent(notice);
    expect(row).not.toHaveTextContent("0.00 EUR");
  });
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSettlement).mockReset().mockResolvedValue(structuredClone(consumptionDraft));
    vi.mocked(api.listCostEntries).mockResolvedValue([createCostEntry({ tenant_distribution_key: "VERBRAUCH" })]);
    vi.mocked(api.listUnits).mockResolvedValue([
      { id: "unit-1", number: "1", location: "EG" }, { id: "unit-2", number: "2", location: "OG" },
    ] as never);
    vi.mocked(api.getConsumptionSuggestions).mockReset().mockResolvedValue(structuredClone(consumptionRows));
    vi.mocked(api.computeSettlement).mockResolvedValue(structuredClone(consumptionDraft));
  });
  it.each([["VERBRAUCH", "Verbrauch"], ["WOHNFLAECHE", "Flaeche"], ["BEHEIZTE_FLAECHE", "Beheizte Flaeche"], ["EINHEIT", "Einheiten"], ["PERSONENZAHL", "Personen"], ["EXTERNE_ABRECHNUNG", "Extern"], ["MANUELLER_BETRAG", "Manuell"]])("renders canonical key %s as %s", async (key, label) => {
    vi.mocked(api.getSettlement).mockResolvedValue({ ...consumptionDraft, total_cost: "30.00", balance: "30.00", lines: [{
      id: "line", cost_entry_id: "water", line_type: "COST", cost_category_code: "KALTWASSER", label: "Kaltwasser",
      distribution_key: key, total_amount: "100.00", distribution_base_total: "100.000000", distribution_base_share: "30.000000",
      time_share_days: 365, time_share_total_days: 365, amount: "30.00", sort_order: 0, note: "",
    }] });
    showProposal();
    expect(await screen.findByRole("cell", { name: label })).toBeVisible();
  });
  it("prefills every authorized unit and explains the actual reading basis", async () => {
    const first = await showConsumption();
    await waitFor(() => expect(first).toHaveValue("30.0000"));
    expect(screen.getByLabelText(/^Verbrauch \(2/)).toHaveValue("70.0000");
    expect(screen.getAllByText(/Zählerbasis:/)).toHaveLength(2);
    expect(api.getConsumptionSuggestions).toHaveBeenCalledWith("run-1");
  });
  it("explains the property-year basis for manual consumption and persons in a clipped run", async () => {
    vi.mocked(api.getSettlement).mockResolvedValue({ ...consumptionDraft, period_end: "2025-09-30" });
    vi.mocked(api.listCostEntries).mockResolvedValue([
      createCostEntry({ tenant_distribution_key: "VERBRAUCH" }),
      createCostEntry({ id: "persons", tenant_distribution_key: "PERSONENZAHL" }),
    ]);
    await showConsumption();
    expect(screen.getByText("Verbrauch der Einheit im gesamten Objekt-Abrechnungsjahr eingeben, auch bei gekürztem Mietzeitraum." )).toBeVisible();
    expect(screen.getByText("Personenzahlen je Einheit für das gesamte Objekt-Abrechnungsjahr in allen zugehörigen Abrechnungen gleich eingeben." )).toBeVisible();
  });
  it("shows a day fallback reason alongside the actual meter basis", async () => {
    vi.mocked(api.getConsumptionSuggestions).mockResolvedValue(consumptionRows.map(row => ({
      ...row, reason: "Zwischenablesung fehlt: Aufteilung des Einheitsverbrauchs nach Tagen.",
    })));
    await showConsumption();
    expect(await screen.findAllByText(/Zwischenablesung fehlt:/)).toHaveLength(2);
  });
  it("does not echo automatic prefill as manual input", async () => {
    const first = await showConsumption();
    await waitFor(() => expect(first).toHaveValue("30.0000"));
    fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
    await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledWith("run-1", {
      heating_inputs: [], external_amounts: [], manual_amounts: [], unit_metrics: [],
    }));
  });
  it("sends an explicitly entered manual zero and no automatic peer value", async () => {
    const first = await showConsumption();
    await waitFor(() => expect(first).toHaveValue("30.0000"));
    fireEvent.change(first, { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
    await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledWith("run-1", {
      heating_inputs: [], external_amounts: [], manual_amounts: [], unit_metrics: [{ unit_id: "unit-1", consumption: "0", person_count: null }],
    }));
  });
  it("keeps a cleared consumption field visible", async () => {
    const first = await showConsumption();
    fireEvent.change(first, { target: { value: "9" } });
    fireEvent.change(first, { target: { value: "" } });
    expect(screen.getByLabelText(/^Verbrauch \(1/)).toBeVisible();
    expect(screen.getByLabelText(/^Verbrauch \(1/)).toHaveValue("");
  });
  it("shows source loading failures and retries without inventing zero", async () => {
    vi.mocked(api.getConsumptionSuggestions).mockRejectedValueOnce(new ApiError(403, "Zählerbasis gesperrt"));
    await showConsumption();
    expect(await screen.findByRole("alert")).toHaveTextContent("Zählerbasis gesperrt");
    expect(screen.getByRole("button", { name: "Berechnen" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Zählerbasis erneut laden" }));
    await waitFor(() => expect(screen.getByLabelText(/^Verbrauch \(1/)).toHaveValue("30.0000"));
    expect(screen.getByRole("button", { name: "Berechnen" })).toBeEnabled();
  });
  it("ignores an obsolete meter response after switching settlement", async () => {
    const pending = deferred<ConsumptionSuggestion[]>();
    vi.mocked(api.getSettlement).mockResolvedValueOnce(structuredClone(consumptionDraft))
      .mockResolvedValueOnce({ ...consumptionDraft, id: "run-2" });
    vi.mocked(api.getConsumptionSuggestions).mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce(consumptionRows.map(row => ({ ...row, consumption: "5.0000" })));
    showProposal();
    await waitFor(() => expect(api.getConsumptionSuggestions).toHaveBeenCalledWith("run-1"));
    fireEvent.click(screen.getByRole("link", { name: "Andere Abrechnung" }));
    fireEvent.click(await screen.findByRole("button", { name: "Strukturierte Eingaben anzeigen" }));
    await waitFor(() => expect(screen.getByLabelText(/^Verbrauch \(1/)).toHaveValue("5.0000"));
    await act(async () => pending.resolve(structuredClone(consumptionRows)));
    expect(screen.getByLabelText(/^Verbrauch \(1/)).toHaveValue("5.0000");
    expect(api.getConsumptionSuggestions).toHaveBeenLastCalledWith("run-2");
  });
  it("does not set meter source errors after unmount", async () => {
    const pending = deferred<ConsumptionSuggestion[]>();
    vi.mocked(api.getConsumptionSuggestions).mockReturnValueOnce(pending.promise);
    showProposal();
    await waitFor(() => expect(api.getConsumptionSuggestions).toHaveBeenCalledWith("run-1"));
    cleanup();
    await act(async () => pending.reject(new ApiError(403, "Late source failure")));
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("retains an explicit manual zero after a failed calculation and retries it", async () => {
    vi.mocked(api.computeSettlement).mockRejectedValueOnce(new ApiError(422, "Ablesungen prüfen"));
    const first = await showConsumption();
    fireEvent.change(first, { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Ablesungen prüfen");
    expect(first).toHaveValue("0");
    fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
    await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledTimes(2));
    expect(api.computeSettlement).toHaveBeenLastCalledWith("run-1", {
      heating_inputs: [], external_amounts: [], manual_amounts: [], unit_metrics: [{ unit_id: "unit-1", consumption: "0", person_count: null }],
    });
  });
});
