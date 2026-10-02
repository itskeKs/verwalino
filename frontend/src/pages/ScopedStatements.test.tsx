import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MietkontoPage } from "./MietkontoPage";
import { OpenItemsPage } from "./OpenItemsPage";
import * as endpoints from "../api/endpoints";

vi.mock("../api/endpoints", () => ({
  listLeases: vi.fn(), listTenantProfiles: vi.fn(), listOwnerProfiles: vi.fn(), listPersons: vi.fn(),
  listUnits: vi.fn(), listProperties: vi.fn(), listEconomicPlans: vi.fn(),
  leaseStatement: vi.fn(), ownerStatement: vi.fn(), allLeaseOpenItems: vi.fn(),
}));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => {
  vi.resetAllMocks();
  const resolved = (name: "listLeases" | "listTenantProfiles" | "listOwnerProfiles" | "listPersons" | "listUnits" | "listProperties" | "listEconomicPlans", value: unknown) => vi.mocked(endpoints[name]).mockResolvedValue(value as never);
  resolved("listLeases", [{ id: "lease", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-01-01" }, { id: "other", unit_id: "other-unit", tenant_profile_id: "tenant", valid_from: "2025-01-01" }]);
  resolved("listTenantProfiles", [{ id: "tenant", person_id: "person" }]);
  resolved("listOwnerProfiles", [{ id: "owner", person_id: "person" }]);
  resolved("listPersons", [{ id: "person", first_name: "Test", last_name: "Person" }]);
  resolved("listUnits", [{ id: "unit", property_id: "property", number: "1" }, { id: "other-unit", property_id: "other-property", number: "2" }]);
  resolved("listProperties", [{ id: "property", name: "Testhaus" }, { id: "other-property", name: "Anderes Haus" }]);
  resolved("listEconomicPlans", [{ id: "plan", property_id: "property", plan_year: 2025, status: "finalized" }]);
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const params = new URL(input, "http://test").searchParams;
    expect(params.get("capability")).toBe("read");
    return new Response(JSON.stringify({ items: [{ id: "scope", label: "Lesebereich", scope_type: params.get("scope_type"), valid_from: "2025-01-01", valid_to: null }], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
});
const statement = { soll_total: "7200.00", ist_total: "100.00", balance: "-7100.00", open_items: [] };

it("passes the selected read scope and clears stale results and scope when changing lease", async () => {
  vi.mocked(endpoints.leaseStatement).mockResolvedValue(statement);
  render(<MietkontoPage />);
  fireEvent.change(await screen.findByLabelText("Mietvertrag *"), { target: { value: "lease" } });
  await screen.findByRole("option", { name: /Lesebereich/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope" } });
  fireEvent.click(screen.getByRole("button", { name: "Abrufen" }));
  await screen.findByText("100.00 EUR");
  expect(endpoints.leaseStatement).toHaveBeenCalledWith("lease", expect.any(String), expect.any(String), "scope");
  fireEvent.change(screen.getByLabelText("Mietvertrag *"), { target: { value: "other" } });
  expect(screen.queryByText("100.00 EUR")).toBeNull();
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("");
});

it("passes owner scope and plan and freezes query controls until the response arrives", async () => {
  let finish!: (value: typeof statement) => void;
  vi.mocked(endpoints.ownerStatement).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  render(<MietkontoPage />);
  fireEvent.change(await screen.findByLabelText("Ansicht"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("Eigentuemer *"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("Wirtschaftsplan (fuer Soll)"), { target: { value: "plan" } });
  await screen.findByRole("option", { name: /Lesebereich/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope" } });
  fireEvent.click(screen.getByRole("button", { name: "Abrufen" }));
  expect(screen.getByLabelText("Ansicht")).toBeDisabled();
  expect(screen.getByLabelText("Zeitraum von *")).toBeDisabled();
  expect(endpoints.ownerStatement).toHaveBeenCalledWith("owner", expect.any(String), expect.any(String), "plan", "scope");
  await act(async () => finish(statement));
  await screen.findByText("100.00 EUR");
  fireEvent.change(screen.getByLabelText("Zeitraum von *"), { target: { value: "2025-01-01" } });
  expect(screen.queryByText("100.00 EUR")).toBeNull();
});

it("never turns a blocked open-items query into an empty success and can retry the same scope", async () => {
  vi.mocked(endpoints.allLeaseOpenItems).mockRejectedValueOnce(new Error("Scope incomplete")).mockResolvedValueOnce([]);
  render(<MemoryRouter><OpenItemsPage /></MemoryRouter>);
  await screen.findByRole("option", { name: /Lesebereich/ });
  expect(screen.queryByText(/Keine offenen Posten/)).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope" } });
  fireEvent.click(screen.getByRole("button", { name: "Offene Posten laden" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Scope incomplete");
  expect(screen.queryByText(/Keine offenen Posten/)).toBeNull();
  expect(endpoints.allLeaseOpenItems).toHaveBeenLastCalledWith(expect.any(String), expect.any(String), "scope");
  fireEvent.click(screen.getByRole("button", { name: "Offene Posten laden" }));
  await screen.findByText("Keine offenen Posten im abgefragten Bereich und Zeitraum.");
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("scope");
  fireEvent.change(screen.getByLabelText("Zeitraum bis *"), { target: { value: "2025-12-31" } });
  await waitFor(() => expect(screen.queryByText(/Keine offenen Posten/)).toBeNull());
});
