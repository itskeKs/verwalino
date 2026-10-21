import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { DunningNoticesPage } from "./DunningNoticesPage";
import { DunningNoticeDetailPage } from "./DunningNoticeDetailPage";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";

vi.mock("../api/endpoints", () => ({
  listDunningNotices: vi.fn(), createDunningForTenant: vi.fn(), createDunningForOwner: vi.fn(),
  listTenantProfiles: vi.fn(), listOwnerProfiles: vi.fn(), listPersons: vi.fn(), listLeases: vi.fn(),
  listProperties: vi.fn(), listUnits: vi.fn(), listEconomicPlans: vi.fn(), getDunningNotice: vi.fn(), issueDunningNotice: vi.fn(),
  deleteDunningNotice: vi.fn(), dunningPdfUrl: vi.fn(),
}));
const scope = { id: "scope", property_id: "property", label: "Mietkreis", scope_type: "MIET", valid_from: "2025-01-01", valid_to: null };
const notice = { id: "notice", accounting_scope_id: "scope", tenant_profile_id: "tenant", status: "draft", level: 1, principal_amount: "7100.00", fee_amount: "0.00", total_amount: "7100.00", included_items: [] };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(endpoints.listProperties).mockResolvedValue([{ id: "property", name: "Sonnenhof", management_type: "WEG" }] as never);
  const resolved = (name: "listLeases" | "listTenantProfiles" | "listOwnerProfiles" | "listPersons" | "listUnits" | "listEconomicPlans" | "listDunningNotices", value: unknown) => vi.mocked(endpoints[name]).mockResolvedValue(value as never);
  resolved("listLeases", [{ id: "lease", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-01-01" }, { id: "other", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-02-01" }]);
  resolved("listTenantProfiles", [{ id: "tenant", person_id: "person" }]);
  resolved("listOwnerProfiles", [{ id: "owner", person_id: "person" }]);
  resolved("listPersons", [{ id: "person", first_name: "Test", last_name: "Person" }]);
  resolved("listUnits", [{ id: "unit", property_id: "property", number: "1" }]);
  resolved("listEconomicPlans", [{ id: "plan", property_id: "property", plan_year: 2025, status: "finalized" }]);
  resolved("listDunningNotices", []);
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = new URL(input, "http://test");
    if (url.pathname.endsWith("/scope")) { expect(url.searchParams.get("capability")).toBe("prepare"); return new Response(JSON.stringify(scope)); }
    expect(url.searchParams.get("capability")).toBe("prepare");
    if (url.searchParams.get("property_id") !== "property") return new Response(JSON.stringify({ items: [], next_cursor: null }));
    return new Response(JSON.stringify({ items: [{ ...scope, scope_type: url.searchParams.get("scope_type") }], next_cursor: null }));
  }));
});

it("preserves and displays the open-items scope, retains it after an error, and clears it on source changes", async () => {
  vi.mocked(endpoints.createDunningForTenant).mockRejectedValue(new ApiError(409, "Review history"));
  render(<MemoryRouter initialEntries={["/mahnungen?create=tenant&tenant_profile_id=tenant&lease_agreement_id=lease&period_start=2025-01-01&period_end=2025-12-31&accounting_scope_id=scope"]}><DunningNoticesPage /></MemoryRouter>);
  const selector = await screen.findByRole("combobox", { name: "Abrechnungskreis" });
  await waitFor(() => expect(selector).toHaveValue("scope"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Mahnung erstellen" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Mahnung erstellen" }));
  await screen.findByText("Review history");
  expect(endpoints.createDunningForTenant).toHaveBeenCalledWith(expect.objectContaining({ accounting_scope_id: "scope", lease_agreement_id: "lease", period_start: "2025-01-01" }));
  expect(selector).toHaveValue("scope");
  fireEvent.change(screen.getByLabelText("Mietvertrag *"), { target: { value: "other" } });
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("");
});

it("passes the explicit owner plan and WEG preparation scope", async () => {
  vi.mocked(endpoints.createDunningForOwner).mockRejectedValue(new ApiError(422, "Plan not finalized"));
  render(<MemoryRouter><DunningNoticesPage /></MemoryRouter>);
  await screen.findByText("Keine Mahnungen vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Mahnung" }));
  fireEvent.change(screen.getByLabelText("Empfaengertyp *"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("Eigentuemer *"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("Wirtschaftsplan"), { target: { value: "plan" } });
  await screen.findByRole("option", { name: /Mietkreis · WEG/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope" } });
  fireEvent.click(screen.getByRole("button", { name: "Mahnung erstellen" }));
  await screen.findByText("Plan not finalized");
  expect(endpoints.createDunningForOwner).toHaveBeenCalledWith(expect.objectContaining({ accounting_scope_id: "scope", economic_plan_id: "plan", owner_profile_id: "owner" }));
});

it("does not present unavailable notices as an empty result and offers retry", async () => {
  vi.mocked(endpoints.listDunningNotices).mockRejectedValueOnce(new Error("Unavailable")).mockResolvedValueOnce([]);
  render(<MemoryRouter><DunningNoticesPage /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Unavailable");
  expect(screen.queryByText("Keine Mahnungen vorhanden.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));
  await screen.findByText("Keine Mahnungen vorhanden.");
});

it("hides the loaded snapshot after a denied issue and presents the scoped cancellation action", async () => {
  vi.mocked(endpoints.getDunningNotice).mockResolvedValue(notice as never);
  vi.mocked(endpoints.issueDunningNotice).mockRejectedValue(new ApiError(404, "Dunning notice not accessible"));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByRole("button", { name: "Entwurf verwerfen" });
  fireEvent.click(screen.getByRole("button", { name: /ausgeben/i }));
  await screen.findByRole("alert");
  expect(screen.queryByText("7100.00 EUR")).toBeNull();
  expect(screen.queryByRole("button", { name: "Entwurf verwerfen" })).toBeNull();
});


it("blocks delayed mismatched prefill and failed scope hydration without silently switching to legacy", async () => {
  let finishUnits!: (rows: Awaited<ReturnType<typeof endpoints.listUnits>>) => void;
  vi.mocked(endpoints.listUnits).mockReturnValue(new Promise(resolve => { finishUnits = resolve; }));
  render(<MemoryRouter initialEntries={["/mahnungen?create=tenant&tenant_profile_id=tenant&lease_agreement_id=lease&accounting_scope_id=scope"]}><DunningNoticesPage /></MemoryRouter>);
  const submit = await screen.findByRole("button", { name: "Mahnung erstellen" });
  expect(submit).toBeDisabled();
  await act(async () => finishUnits([{ id: "unit", property_id: "different-property", number: "1" }] as never));
  expect(await screen.findByRole("alert")).toHaveTextContent("passt nicht zur Auswahl");
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("scope");
  expect(screen.getByRole("option", { name: /Übernommener Kreis nicht verfügbar/ })).toBeInTheDocument();
  expect(submit).toBeDisabled();
  fireEvent.submit(submit.closest("form")!);
  expect(endpoints.createDunningForTenant).not.toHaveBeenCalled();
});

it("blocks a read-only inherited scope lacking preparation access and keeps its failed selection visible", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => new URL(input, "http://test").pathname.endsWith("/scope")
    ? new Response(JSON.stringify({ detail: "Not accessible" }), { status: 404 })
    : new Response(JSON.stringify({ items: [], next_cursor: null }))));
  render(<MemoryRouter initialEntries={["/mahnungen?create=tenant&tenant_profile_id=tenant&lease_agreement_id=lease&accounting_scope_id=scope"]}><DunningNoticesPage /></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Abrechnungskreis nicht verfügbar");
  const submit = screen.getByRole("button", { name: "Mahnung erstellen" });
  expect(submit).toBeDisabled();
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("scope");
  fireEvent.submit(submit.closest("form")!);
  expect(endpoints.createDunningForTenant).not.toHaveBeenCalled();
});


it("creates owner dunning for frozen special levies without an economic plan", async () => {
  vi.mocked(endpoints.listEconomicPlans).mockResolvedValue([]);
  vi.mocked(endpoints.createDunningForOwner).mockRejectedValue(new ApiError(409, "Prüfung behalten"));
  render(<MemoryRouter><DunningNoticesPage /></MemoryRouter>);
  await screen.findByText("Keine Mahnungen vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Mahnung" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Empfaengertyp *" }), { target: { value: "owner" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Eigentuemer *" }), { target: { value: "owner" } });
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "property" } });
  await screen.findByRole("option", { name: /Mietkreis · WEG/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope" } });
  fireEvent.click(screen.getByRole("button", { name: "Mahnung erstellen" }));
  await screen.findByText("Prüfung behalten");
  expect(endpoints.createDunningForOwner).toHaveBeenCalledWith(expect.objectContaining({ owner_profile_id: "owner", accounting_scope_id: "scope", economic_plan_id: null }));
  expect(endpoints.createDunningForOwner).toHaveBeenCalledWith(expect.not.objectContaining({ property_id: expect.anything() }));
  fireEvent.change(screen.getByRole("combobox", { name: "WEG-Objekt *" }), { target: { value: "" } });
  expect(screen.queryByRole("combobox", { name: "Abrechnungskreis" })).toBeNull();
});
