import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import * as api from "../api/endpoints";
import { SettlementDetailPage } from "./SettlementDetailPage";

vi.mock("../api/endpoints", async importOriginal => ({
  ...await importOriginal<typeof import("../api/endpoints")>(),
  getSettlement: vi.fn(), listProperties: vi.fn().mockResolvedValue([]), listLeases: vi.fn().mockResolvedValue([]),
  listUnits: vi.fn().mockResolvedValue([]), listTenantProfiles: vi.fn().mockResolvedValue([]), listPersons: vi.fn().mockResolvedValue([]),
  listCostCategories: vi.fn(), listCostEntries: vi.fn(), getConsumptionSuggestions: vi.fn(), getHeatingConsumption: vi.fn(),
  computeSettlement: vi.fn(), previewSettlement: vi.fn(), finalizeSettlement: vi.fn(),
}));
vi.mock("../components/FinancialCoverageReview", () => ({ FinancialCoverageReview: () => null }));
const run = { id: "run-1", property_id: "property", unit_id: "unit-1", status: "draft", run_type: "BK_MIETER",
  period_start: "2025-01-01", period_end: "2025-12-31", lines: [], extra_inputs: null };
const rows = [
  { unit_id: "unit-1", unit_label: "1", period_from: "2025-01-01", period_to: "2025-06-30", base_quantity: "50.00", consumption: "10.00", meter_id: "meter", reason: null },
  { unit_id: "unit-1", unit_label: "1", period_from: "2025-07-01", period_to: "2025-12-31", base_quantity: "50.00", consumption: "20.00", meter_id: "meter", reason: "Leerstand" },
];
const config = { cost_entry_id: "heat", kind: "HEATING", consumption_fraction: "0.65", base_key: "AREA", consumption_recorded: true,
  mandatory_seventy_percent: false, meter_type: "HEIZUNG", slices: [] };
function show(role = "OWNER_ADMIN") {
  return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={["/abrechnungen/run-1"]}><Routes><Route path="/abrechnungen/:runId" element={<SettlementDetailPage />} /></Routes></MemoryRouter>
  </AuthCtx.Provider>);
}
function change(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
async function activate() {
  fireEvent.click(await screen.findByLabelText("Intern nach HeizkostenV abrechnen: Heizung"));
  change("Verbrauchsanteil (0,50–0,70)", "0,65"); change("Zählertyp", "HEIZUNG");
  await screen.findByLabelText("Heizverbrauch 1 2025-01-01–2025-06-30");
  await waitFor(() => expect(screen.getByRole("button", { name: "Berechnen" })).toBeEnabled());
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getSettlement).mockResolvedValue(run as never);
  vi.mocked(api.listCostCategories).mockResolvedValue([{ id: "cat", code: "HEIZUNG_ZENTRAL", label: "Heizung" }] as never);
  vi.mocked(api.listCostEntries).mockResolvedValue([{ id: "heat", cost_category_id: "cat", tenant_distribution_key: "EXTERNE_ABRECHNUNG" },
    { id: "water", cost_category_id: "water", tenant_distribution_key: "VERBRAUCH" }] as never);
  vi.mocked(api.getConsumptionSuggestions).mockResolvedValue([{ unit_id: "unit-1", unit_label: "1", consumption: "99", reason: null }] as never);
  vi.mocked(api.getHeatingConsumption).mockReset().mockResolvedValue(rows);
  vi.mocked(api.computeSettlement).mockResolvedValue(run as never);
  vi.mocked(api.previewSettlement).mockResolvedValue(run as never);
  vi.mocked(api.finalizeSettlement).mockResolvedValue({ ...run, status: "finalized" } as never);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("loads only after activation, preserves tenant/vacancy slices and sends only exact manual overrides independently of generic consumption", async () => {
  show(); await screen.findByRole("button", { name: "Berechnen" }); expect(api.getHeatingConsumption).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Strukturierte Eingaben anzeigen" }));
  change("HEIZUNG_ZENTRAL Heizung", "123.45");
  await activate();
  expect(api.getHeatingConsumption).toHaveBeenLastCalledWith("run-1", "heat", "HEIZUNG", "AREA");
  expect(screen.getByText("Leerstand")).toBeVisible();
  change("Heizverbrauch 1 2025-01-01–2025-06-30", "0");
  change("Grundmenge 1 2025-07-01–2025-12-31", "9007199254740993,01");
  expect(screen.queryByLabelText("HEIZUNG_ZENTRAL Heizung")).toBeNull();
  change("Verbrauch (1)", "7,25");
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledWith("run-1", {
    external_amounts: [], manual_amounts: [], unit_metrics: [{ unit_id: "unit-1", consumption: "7.25", person_count: null }],
    heating_inputs: [{ ...config, slices: [
      { unit_id: "unit-1", period_from: rows[0].period_from, period_to: rows[0].period_to, consumption: "0" },
      { unit_id: "unit-1", period_from: rows[1].period_from, period_to: rows[1].period_to, base_quantity: "9007199254740993.01" },
    ] }],
  }));
});
it("blocks all actions while candidates load or fail, then retries", async () => {
  let reject!: (reason: Error) => void;
  vi.mocked(api.getHeatingConsumption).mockReturnValueOnce(new Promise((_, no) => { reject = no; }));
  show(); fireEvent.click(await screen.findByLabelText("Intern nach HeizkostenV abrechnen: Heizung"));
  change("Verbrauchsanteil (0,50–0,70)", "0.65"); change("Zählertyp", "HEIZUNG");
  for (const name of ["Berechnen", "Vorschau", "Finalisieren"]) expect(screen.getByRole("button", { name })).toBeDisabled();
  await act(async () => reject(new Error("Zähler nicht erreichbar")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Zähler nicht erreichbar");
  fireEvent.click(screen.getByRole("button", { name: "Heizbasis erneut laden" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Berechnen" })).toBeEnabled());
});
it("restores saved config and overrides on reload, and explicitly clears internal intent when disabled", async () => {
  vi.mocked(api.getSettlement).mockResolvedValue({ ...run, extra_inputs: { external_amounts: [], manual_amounts: [], unit_metrics: [],
    heating_inputs: [{ ...config, slices: [{ unit_id: "unit-1", period_from: rows[0].period_from, period_to: rows[0].period_to, consumption: "0" }] }] } } as never);
  const view = show(); expect(await screen.findByLabelText("Heizverbrauch 1 2025-01-01–2025-06-30")).toHaveValue("0");
  view.unmount(); show(); expect(await screen.findByLabelText("Heizverbrauch 1 2025-01-01–2025-06-30")).toHaveValue("0");
  fireEvent.click(screen.getByLabelText("Intern nach HeizkostenV abrechnen: Heizung"));
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledWith("run-1", expect.objectContaining({ heating_inputs: [] })));
});
it("requires manual VOLUME bases for every slice and warns of 15% deduction without consumption fields", async () => {
  show(); await activate(); change("Grundkostenschlüssel", "VOLUME");
  await waitFor(() => expect(api.getHeatingConsumption).toHaveBeenLastCalledWith("run-1", "heat", "HEIZUNG", "VOLUME"));
  expect(screen.getByRole("button", { name: "Berechnen" })).toBeDisabled();
  change("Grundmenge 1 2025-01-01–2025-06-30", "100,5"); change("Grundmenge 1 2025-07-01–2025-12-31", "101");
  fireEvent.click(screen.getByLabelText("Verbrauch erfasst"));
  expect(screen.getByText(/15 %.*Kürzung/)).toBeVisible(); expect(screen.queryByLabelText(/^Heizverbrauch/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau" }));
  await waitFor(() => expect(api.previewSettlement).toHaveBeenCalledWith("run-1", expect.objectContaining({ heating_inputs: [expect.objectContaining({ consumption_recorded: false, base_key: "VOLUME", slices: [expect.objectContaining({ base_quantity: "100.5" }), expect.objectContaining({ base_quantity: "101" })] })] })));
});
it("ignores obsolete candidate responses after changing the meter selector", async () => {
  show(); await activate();
  let resolveOld!: (value: typeof rows) => void;
  vi.mocked(api.getHeatingConsumption).mockReturnValueOnce(new Promise(yes => { resolveOld = yes; })).mockResolvedValueOnce(rows.map(row => ({ ...row, consumption: "42" })));
  change("Zählertyp", "OLD");
  await waitFor(() => expect(api.getHeatingConsumption).toHaveBeenLastCalledWith("run-1", "heat", "OLD", "AREA"));
  change("Zählertyp", "NEW");
  await waitFor(() => expect(screen.getByLabelText("Heizverbrauch 1 2025-01-01–2025-06-30")).toHaveValue("42"));
  await act(async () => resolveOld(rows.map(row => ({ ...row, consumption: "999" }))));
  expect(screen.getByLabelText("Heizverbrauch 1 2025-01-01–2025-06-30")).toHaveValue("42");
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledWith("run-1", expect.objectContaining({ heating_inputs: [expect.objectContaining({ meter_type: "NEW", slices: [] })] })));
});
it("validates the fraction and special70 explicitly and sends current intent to finalization", async () => {
  show(); await activate();
  for (const fraction of ["0.49", "0.701", "NaN", "0.6e0", ""]) {
    change("Verbrauchsanteil (0,50–0,70)", fraction);
    expect(screen.getByRole("button", { name: "Berechnen" })).toBeDisabled();
  }
  change("Verbrauchsanteil (0,50–0,70)", "0,65");
  fireEvent.click(screen.getByLabelText("§ 7 Sonderfall: zwingend 70 % Verbrauchsanteil"));
  expect(screen.getByRole("button", { name: "Finalisieren" })).toBeDisabled();
  change("Verbrauchsanteil (0,50–0,70)", "0,70");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Finalisieren" }));
  await waitFor(() => expect(api.finalizeSettlement).toHaveBeenCalledWith("run-1", expect.objectContaining({ heating_inputs: [{ ...config, consumption_fraction: "0.70", mandatory_seventy_percent: true }] })));
});
it("keeps heating overrides and intent after failed compute and offers retry", async () => {
  show(); await activate(); change("Heizverbrauch 1 2025-01-01–2025-06-30", "0");
  vi.mocked(api.computeSettlement).mockRejectedValueOnce(new Error("Heizprüfung fehlgeschlagen"));
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Heizprüfung fehlgeschlagen");
  expect(screen.getByLabelText("Heizverbrauch 1 2025-01-01–2025-06-30")).toHaveValue("0");
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.computeSettlement).mock.calls[0][1]).toEqual(vi.mocked(api.computeSettlement).mock.calls[1][1]);
});
it("keeps heating and hot-water configuration independent per cost entry", async () => {
  vi.mocked(api.listCostCategories).mockResolvedValue([{ id: "cat", code: "HEIZUNG_ZENTRAL", label: "Heizung" }, { id: "warm", code: "WARMWASSER_ZENTRAL", label: "Warmwasser" }] as never);
  vi.mocked(api.listCostEntries).mockResolvedValue([{ id: "heat", cost_category_id: "cat", tenant_distribution_key: "EXTERNE_ABRECHNUNG" }, { id: "hot", cost_category_id: "warm", tenant_distribution_key: "EXTERNE_ABRECHNUNG" }] as never);
  show(); await activate();
  fireEvent.click(screen.getByLabelText("Intern nach HeizkostenV abrechnen: Warmwasser"));
  const warm = screen.getByRole("group", { name: "Warmwasser" });
  fireEvent.change(warm.querySelector('input[inputmode="decimal"]')!, { target: { value: "0,50" } });
  fireEvent.change(warm.querySelector('input[list]')!, { target: { value: "WARMWASSER" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Berechnen" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledWith("run-1", expect.objectContaining({ unit_metrics: [], external_amounts: [], heating_inputs: [config, { ...config, cost_entry_id: "hot", kind: "HOT_WATER", consumption_fraction: "0.50", meter_type: "WARMWASSER" }] })));
});
it("restores saved external, manual and generic overrides as well as internal intent", async () => {
  const saved = { external_amounts: [{ cost_entry_id: "external", unit_id: "unit-1", tenant_amount: "12.34" }], manual_amounts: [{ cost_entry_id: "manual", unit_id: "unit-1", amount: "5.67" }], unit_metrics: [{ unit_id: "unit-1", consumption: "0", person_count: 2 }], heating_inputs: [config] };
  vi.mocked(api.getSettlement).mockResolvedValue({ ...run, extra_inputs: saved } as never);
  vi.mocked(api.listCostEntries).mockResolvedValue([{ id: "heat", cost_category_id: "cat", tenant_distribution_key: "EXTERNE_ABRECHNUNG" }, { id: "external", tenant_distribution_key: "EXTERNE_ABRECHNUNG" }, { id: "manual", tenant_distribution_key: "MANUELLER_BETRAG" }, { id: "water", tenant_distribution_key: "VERBRAUCH" }, { id: "persons", tenant_distribution_key: "PERSONENZAHL" }] as never);
  show(); fireEvent.click(await screen.findByRole("button", { name: "Strukturierte Eingaben anzeigen" }));
  expect(await screen.findByLabelText("external")).toHaveValue("12.34"); expect(screen.getByLabelText("manual")).toHaveValue("5.67");
  expect(screen.getByLabelText("Verbrauch (1)")).toHaveValue("0"); expect(screen.getByLabelText("Personenzahl (1)")).toHaveValue(2);
  await waitFor(() => expect(screen.getByRole("button", { name: "Berechnen" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await waitFor(() => expect(api.computeSettlement).toHaveBeenCalledWith("run-1", saved));
});
it.each(["READ_ONLY", "UNKNOWN"])("does not offer internal heating controls to %s", async role => {
  show(role); await screen.findByRole("heading", { level: 1 });
  expect(screen.queryByLabelText("Intern nach HeizkostenV abrechnen: Heizung")).toBeNull(); expect(api.getHeatingConsumption).not.toHaveBeenCalled();
});
