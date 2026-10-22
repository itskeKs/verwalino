import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { DashboardKpis } from "./DashboardKpis";
import { getDashboardKpis, type DashboardKpiResult } from "../api/dashboardKpis";
import { ApiError } from "../api/client";

vi.mock("../api/dashboardKpis", () => ({ getDashboardKpis: vi.fn() }));
vi.mock("./AccountingScopeSelector", () => ({ AccountingScopeSelector: ({ propertyId, capability, value, onChange, onValidityChange }: { propertyId: string; capability: string; value: string | null; onChange: (id: string | null) => void; onValidityChange: (valid: boolean) => void }) => <select aria-label="KPI-Abrechnungskreis" data-property={propertyId} data-capability={capability} value={value ?? ""} onChange={e => { onChange(e.target.value || null); onValidityChange(e.target.value !== "invalid"); }}><option value="">Wählen</option><option value="scope">Lesekreis</option><option value="invalid">Ungültig</option></select> }));
const properties = [{ id: "p", name: "Sonnenhof" }, { id: "other", name: "Nebenhaus" }];
const result: DashboardKpiResult = { property_id: "p", property_name: "Sonnenhof", year: 2026, month: 10, period_start: "2026-10-01", period_end: "2026-10-31", accounting_scope_id: null, occupancy_date: "2026-10-31", payment_basis: "assigned_rent_period", expected_rent: "850.00", received_rent: "650.00", arrears: "7850.00", arrears_count: 2, unit_count: 3, occupied_units: 2, vacant_units: 1, unassigned_rent_count: 0, unassigned_rent_amount: "0.00", footnote: "Aktueller Einheitenbestand; Belegung zum Monatsende." };
function show(requiresScope: boolean | null = false) { return render(<DashboardKpis properties={properties} requiresScope={requiresScope} sessionKey="org:user:READ_ONLY" />); }
function select() {
  fireEvent.change(screen.getByLabelText("Objekt für Mietkennzahlen"), { target: { value: "p" } });
  fireEvent.change(screen.getByLabelText("Mietmonat"), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("Mietjahr"), { target: { value: "2026" } });
}
function load() { fireEvent.click(screen.getByRole("button", { name: "Mietkennzahlen laden" })); }
function money(label: string) { return within(screen.getByRole("group", { name: label })); }
async function loadedMoney(label: string) { return within(await screen.findByRole("group", { name: label })); }
beforeEach(() => { vi.mocked(getDashboardKpis).mockResolvedValue(result); });
afterEach(() => { cleanup(); vi.resetAllMocks(); });
it("requires explicit property and renders exact period-based amounts and inventory for read-only sessions", async () => {
  show(); expect(getDashboardKpis).not.toHaveBeenCalled(); expect(screen.getByRole("button", { name: "Mietkennzahlen laden" })).toBeDisabled();
  select(); load();
  expect(await (await loadedMoney("Soll-Miete")).findByText(/^850,00 €$/)).toBeVisible();
  expect(money("Ist-Miete").getByText(/650,00/)).toBeVisible(); expect(money("Mietrückstände").getByText(/7\.850,00/)).toBeVisible();
  expect(getDashboardKpis).toHaveBeenCalledWith({ property_id: "p", year: 2026, month: 10 }, expect.any(AbortSignal));
  expect(screen.getByText(/Kaltmiete.*Nebenkosten.*zugeordneten Mietmonat/)).toBeVisible();
  expect(screen.getByText(/nicht.*Wertstellung/)).toBeVisible(); expect(screen.getByText(/positive.*Mietkonten.*2/)).toBeVisible();
  expect(screen.getByText(/Einheiten: 3.*Belegt: 2.*Leer: 1/)).toBeVisible(); expect(screen.getByText(/Belegungsstichtag: 31\.10\.2026/)).toBeVisible();
  expect(screen.getByText(result.footnote)).toBeVisible();
});
it.each([null, true])("does not request without known policy / required scope (%s)", async policy => {
  show(policy); select(); load(); expect(getDashboardKpis).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Mietkennzahlen laden" })).toBeDisabled();
});
it("uses a property-filtered READ selector, requires a valid scope and clears it with property", async () => {
  show(true); select(); const scope = screen.getByLabelText("KPI-Abrechnungskreis");
  expect(scope).toHaveAttribute("data-property", "p"); expect(scope).toHaveAttribute("data-capability", "read");
  fireEvent.change(scope, { target: { value: "invalid" } }); load(); expect(getDashboardKpis).not.toHaveBeenCalled();
  fireEvent.change(scope, { target: { value: "scope" } }); load();
  await waitFor(() => expect(getDashboardKpis).toHaveBeenCalledWith(expect.objectContaining({ accounting_scope_id: "scope" }), expect.any(AbortSignal)));
  fireEvent.change(screen.getByLabelText("Objekt für Mietkennzahlen"), { target: { value: "other" } });
  expect(screen.getByLabelText("KPI-Abrechnungskreis")).toHaveValue(""); expect(screen.getByRole("button", { name: "Mietkennzahlen laden" })).toBeDisabled();
});
it.each(["Mietmonat", "Mietjahr", "Objekt für Mietkennzahlen", "KPI-Abrechnungskreis"])("invalidates displayed values immediately when %s changes", async label => {
  show(); select(); load(); await (await loadedMoney("Soll-Miete")).findByText(/^850,00 €$/);
  const value = label === "Mietmonat" ? "11" : label === "Mietjahr" ? "2025" : label === "KPI-Abrechnungskreis" ? "scope" : "other";
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
  expect(screen.queryByText(/^850,00 €$/)).toBeNull();
});
it("aborts obsolete work, discards a late reply, and guards duplicate loading", async () => {
  let resolve!: (value: DashboardKpiResult) => void;
  vi.mocked(getDashboardKpis).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  show(); select(); load(); load(); expect(getDashboardKpis).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("status")).toHaveTextContent(/geladen/);
  const signal = vi.mocked(getDashboardKpis).mock.calls[0][1];
  fireEvent.change(screen.getByLabelText("Mietmonat"), { target: { value: "11" } });
  expect(signal?.aborted).toBe(true);
  vi.mocked(getDashboardKpis).mockResolvedValue({ ...result, month: 11, expected_rent: "900.00" }); load();
  await screen.findByText(/900,00/); await act(async () => resolve(result));
  expect(screen.queryByText(/^850,00 €$/)).toBeNull(); expect(screen.getByText(/900,00/)).toBeVisible();
});
it.each([403, 409, 422])("shows HTTP %s failure without guessed zero and retries", async status => {
  vi.mocked(getDashboardKpis).mockRejectedValueOnce(new ApiError(status, "Historischer Zugriff fehlt"));
  show(); select(); load(); expect(await screen.findByRole("alert")).toHaveTextContent("Historischer Zugriff fehlt");
  expect(screen.getByRole("alert")).toHaveTextContent(String(status)); expect(screen.queryByText(/0,00 €/)).toBeNull();
  load(); await screen.findByText(/^850,00 €$/); expect(screen.queryByRole("alert")).toBeNull();
});
it("warns about unassigned rent without counting it as received rent", async () => {
  vi.mocked(getDashboardKpis).mockResolvedValue({ ...result, unassigned_rent_count: 2, unassigned_rent_amount: "200.00" });
  show(); select(); load(); expect(await screen.findByRole("alert")).toHaveTextContent(/2.*200,00.*keinem Mietmonat zugeordnet/);
  expect(money("Ist-Miete").getByText(/650,00/)).toBeVisible();
});
it("accepts genuine empty zero results without an error", async () => {
  vi.mocked(getDashboardKpis).mockResolvedValue({ ...result, expected_rent: "0.00", received_rent: "0.00", arrears: "0.00", arrears_count: 0, unit_count: 0, occupied_units: 0, vacant_units: 0 });
  show(); select(); load(); expect(await (await loadedMoney("Soll-Miete")).findByText(/0,00/)).toBeVisible(); expect(screen.queryByRole("alert")).toBeNull();
});
it("formats decimal strings without losing cents above Number precision", async () => {
  vi.mocked(getDashboardKpis).mockResolvedValue({ ...result, expected_rent: "9007199254740993.01" });
  show(); select(); load(); expect(await (await loadedMoney("Soll-Miete")).findByText(/9\.007\.199\.254\.740\.993,01/)).toBeVisible();
});
it.each(["", "0", "9999", "2026.5"])("explains invalid year %s and prevents requests", value => {
  show(); select(); fireEvent.change(screen.getByLabelText("Mietjahr"), { target: { value } }); load();
  expect(getDashboardKpis).not.toHaveBeenCalled(); expect(screen.getByText(/Mietjahr.*1.*9998/)).toBeVisible();
});
it("drops data and aborts requests across session changes and unmount", async () => {
  let resolve!: (value: DashboardKpiResult) => void;
  vi.mocked(getDashboardKpis).mockReturnValue(new Promise(r => { resolve = r; }));
  const view = show(); select(); load(); const signal = vi.mocked(getDashboardKpis).mock.calls[0][1];
  view.rerender(<DashboardKpis properties={properties} requiresScope={false} sessionKey="other:user:READ_ONLY" />);
  expect(signal?.aborted).toBe(true); expect(screen.getByLabelText("Objekt für Mietkennzahlen")).toHaveValue("");
  await act(async () => resolve(result)); expect(screen.queryByText(/^850,00 €$/)).toBeNull();
  select(); load(); const secondSignal = vi.mocked(getDashboardKpis).mock.calls.at(-1)?.[1]; view.unmount(); expect(secondSignal?.aborted).toBe(true);
});
