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
    render(<AuthCtx.Provider value={auth("OWNER_ADMIN")}><MemoryRouter initialEntries={["/abrechnungen/run-1"]}>
      <Routes><Route path="/abrechnungen/:runId" element={<SettlementDetailPage />} /></Routes>
    </MemoryRouter></AuthCtx.Provider>);

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

const finalized: SettlementRun = {
  id: "run-1", organization_id: "org", property_id: "property-1", unit_id: "unit-1",
  management_mandate_id: null, lease_agreement_id: "lease-1", tenant_profile_id: "tenant-1",
  run_type: "BK_MIETER", period_start: "2026-01-01", period_end: "2026-12-31",
  status: "finalized", advance_payments_total: "1800.00", advance_payments_source: "MANUAL",
  total_cost: "1200.00", balance: "-600.00", snapshot_sha256: "original-snapshot",
  engine_version: "m3.1.0", finalized_at: "2027-01-01", finalized_by: "user",
  superseded_by_id: null, lines: [],
};
const proposal = {
  lease_agreement_id: "lease-1", tenant_cost: "1200.00", months: "12",
  monthly_amount: "100.00", period_start: "2026-01-01", period_end: "2026-12-31",
};
const originalLease = {
  id: "lease-1", unit_id: "unit-1", tenant_profile_id: "tenant-1", valid_from: "2025-01-01",
  valid_to: null, base_rent: "700.00", advance_payment: "150.00", deposit: null, notes: "Original",
};
const applied = {
  id: "change-1", lease_agreement_id: "lease-1", valid_from: "2027-01-01",
  base_rent: "725.00", advance_payment: "100.00", created_at: "2026-10-20T12:00:00Z",
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
function accept() { fireEvent.click(screen.getByRole("button", { name: "Vorauszahlung übernehmen" })); }

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
    const dateOnly = { valid_from: "2027-01-01", base_rent: "1.00", advance_payment: "999.00" };
    expect(await api.applyAdvanceProposal("run-1", dateOnly)).toEqual(applied);
    expect(fetchProposal.mock.calls.map(([url]) => url)).toEqual([
      "/api/settlements/run-1/advance-proposal", "/api/settlements/run-1/advance-proposal",
    ]);
    expect(JSON.parse(posts()[0][1].body)).toEqual({ valid_from: "2027-01-01" });
  });

  it.each(["OWNER_ADMIN", "CLERK"])("offers basis, date and acceptance to %s without changing lease or snapshot", async (role) => {
    const leaseBefore = structuredClone(originalLease);
    showProposal(role);
    const region = await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" });
    expect(await within(region).findByText("100,00 EUR")).toBeVisible();
    expect(region).toHaveTextContent("Neue Vorauszahlung");
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
    expect(screen.queryByRole("button", { name: "Vorauszahlung übernehmen" })).toBeNull();
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
    await waitFor(() => expect(screen.getByRole("button", { name: "Vorauszahlung übernehmen" })).toBeEnabled());
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
    fetchProposal.mockResolvedValueOnce(new Response(JSON.stringify({ ...proposal, monthly_amount: "", tenant_cost: "" })));
    showProposal();
    const region = await screen.findByRole("region", { name: "Vorauszahlungsvorschlag" });
    expect(await within(region).findByText("Nicht verfügbar")).toBeVisible();
    expect(region).not.toHaveTextContent("0,00 EUR");
  });

  it("disables date/submit and synchronously prevents double submissions", async () => {
    const pending = deferred<Response>();
    showProposal(); await screen.findByText("100,00 EUR"); fetchProposal.mockReturnValueOnce(pending.promise);
    const button = screen.getByRole("button", { name: "Vorauszahlung übernehmen" });
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
