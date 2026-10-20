import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { DepositsPage } from "./DepositsPage";
import type { DepositAccount } from "../api/types";

const auth = vi.hoisted(() => ({ role: "CLERK" }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: auth.role } }) }));
const account: DepositAccount = {
  lease_agreement_id: "lease-1", tenant_profile_id: "tenant-1", unit_id: "unit-1", property_id: "property-1",
  tenant_name: "Ada Mieter", unit_label: "Wohnung 1", property_name: "Sonnenhof",
  expected_amount: "1800.00", paid_amount: "1800.00", refunded_amount: "0.00", held_amount: "1800.00",
};
const lease = { id: "lease-1", valid_from: "2025-01-01", valid_to: null, deposit: "9999.00" };
let rows: DepositAccount[];
let leaseError: Error | null;
let deposits: (url: string) => Promise<DepositAccount[]>;

beforeEach(() => {
  auth.role = "CLERK";
  rows = [account];
  leaseError = null;
  deposits = () => Promise.resolve(rows);
  vi.spyOn(api, "get").mockImplementation(async (url) => {
    if (url.startsWith("/api/payments/deposits")) return deposits(url);
    if (url === "/api/leases") {
      if (leaseError) throw leaseError;
      return [lease, { ...lease, id: "lease-2", valid_from: "2026-01-01" }];
    }
    if (url.startsWith("/api/accounting-scopes/page?")) return { items: [
      { id: "rental", label: "Mietkreis", scope_type: "MIET", valid_from: "2025-01-01", valid_to: null },
      { id: "separate", label: "Separater Kautionskreis", scope_type: "WEG", valid_from: "2025-01-01", valid_to: null },
    ], next_cursor: null };
    throw new Error(`Unexpected GET: ${url}`);
  });
  vi.spyOn(api, "post").mockRejectedValue(new Error("No mutation allowed"));
  vi.spyOn(api, "patch").mockRejectedValue(new Error("No mutation allowed"));
  vi.spyOn(api, "put").mockRejectedValue(new Error("No mutation allowed"));
  vi.spyOn(api, "delete").mockRejectedValue(new Error("No mutation allowed"));
});
afterEach(() => {
  expect(api.post).not.toHaveBeenCalled();
  expect(api.patch).not.toHaveBeenCalled();
  expect(api.put).not.toHaveBeenCalled();
  expect(api.delete).not.toHaveBeenCalled();
  cleanup(); vi.restoreAllMocks();
});
function show() { render(<MemoryRouter><DepositsPage /></MemoryRouter>); }

it("shows persisted 1800 deposit paid in two installments, without using lease deposit as a replacement", async () => {
  show();
  const row = (await screen.findByText("Ada Mieter")).closest("tr")!;
  expect(within(row).getAllByRole("cell").map(cell => cell.textContent)).toEqual([
    "Ada MieterMietvertrag ab 2025-01-01 (laufend)", "Sonnenhof", "Wohnung 1", "1800.00 EUR", "1800.00 EUR", "0.00 EUR", "1800.00 EUR",
  ]);
  expect(screen.getAllByRole("columnheader").map(cell => cell.textContent)).toEqual(["Mieter", "Objekt", "Einheit", "Soll", "Gezahlt", "Zurück", "Bestand"]);
  expect(screen.queryByText(/9999/)).toBeNull();
  expect(api.get).toHaveBeenCalledWith("/api/payments/deposits");
});
it("refreshes after a 300 refund without recomputing paid or expected", async () => {
  show(); await screen.findByText("Ada Mieter");
  rows = [{ ...account, refunded_amount: "300.00", held_amount: "1500.00" }];
  fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
  expect(await screen.findByText("1500.00 EUR")).toBeVisible();
  expect(screen.getByText("300.00 EUR")).toBeVisible();
  expect(screen.getAllByText("1800.00 EUR")).toHaveLength(2);
});
it("preserves money strings beyond float precision without arithmetic", async () => {
  rows = [{ ...account, expected_amount: "9007199254740993.01", held_amount: "0.03" }]; show();
  expect(await screen.findByText("9007199254740993.01 EUR")).toBeVisible();
  expect(screen.getByText("0.03 EUR")).toBeVisible();
});
it.each(["OWNER_ADMIN", "CLERK"])("links %s to the existing payment page only", async role => {
  auth.role = role; show(); await screen.findByText("Ada Mieter");
  expect(screen.getByRole("link", { name: "Zahlung erfassen" })).toHaveAttribute("href", "/zahlungen");
  expect(screen.queryByRole("textbox")).toBeNull();
});
it("lets READ_ONLY read without a write link", async () => {
  auth.role = "READ_ONLY"; show(); await screen.findByText("Ada Mieter");
  expect(screen.queryByRole("link", { name: "Zahlung erfassen" })).toBeNull();
});
it("shows loading and then an empty result", async () => {
  let finish!: (value: DepositAccount[]) => void;
  deposits = () => new Promise(resolve => { finish = resolve; }); show();
  expect(screen.getByRole("status")).toHaveTextContent("Lade");
  expect(screen.queryByText("Keine Kautionen vorhanden.")).toBeNull();
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/payments/deposits"));
  await act(async () => finish([]));
  expect(screen.getByText("Keine Kautionen vorhanden.")).toBeVisible();
});
it.each([403, 409])("shows %s rather than a zero or empty report and supports retry", async status => {
  deposits = () => Promise.reject(new ApiError(status, "Abrechnungskreis prüfen")); show();
  expect(await screen.findByRole("alert")).toHaveTextContent("Abrechnungskreis prüfen");
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.queryByText("Keine Kautionen vorhanden.")).toBeNull();
  deposits = () => Promise.resolve(rows);
  fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
  await screen.findByText("Ada Mieter"); expect(screen.queryByRole("alert")).toBeNull();
});
it("selects read scopes of any type and discards obsolete responses", async () => {
  show(); await screen.findByText("Ada Mieter");
  await screen.findByRole("option", { name: /Mietkreis/ });
  const selector = screen.getByRole("combobox", { name: "Abrechnungskreis" });
  let finish!: (value: DepositAccount[]) => void;
  deposits = url => url.includes("rental") ? new Promise(resolve => { finish = resolve; }) : Promise.resolve([{ ...account, tenant_name: "Neuer Kreis" }]);
  fireEvent.change(selector, { target: { value: "rental" } });
  expect(screen.queryByText("Ada Mieter")).toBeNull();
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/payments/deposits?accounting_scope_id=rental"));
  fireEvent.change(selector, { target: { value: "separate" } });
  await screen.findByText("Neuer Kreis");
  await act(async () => finish([{ ...account, tenant_name: "Veraltet" }]));
  expect(screen.queryByText("Veraltet")).toBeNull();
  expect(api.get).toHaveBeenCalledWith("/api/payments/deposits?accounting_scope_id=separate");
  expect(api.get).toHaveBeenCalledWith("/api/accounting-scopes/page?capability=read&financial_creation=true&q=", expect.any(Object));
  fireEvent.change(selector, { target: { value: "" } });
  await screen.findByText("Neuer Kreis");
  expect(api.get).toHaveBeenLastCalledWith("/api/payments/deposits");
});
it("distinguishes multiple contracts for the same tenant and unit by lease period", async () => {
  rows = [account, { ...account, lease_agreement_id: "lease-2" }]; show();
  expect(await screen.findByText("Mietvertrag ab 2025-01-01 (laufend)")).toBeVisible();
  expect(screen.getByText("Mietvertrag ab 2026-01-01 (laufend)")).toBeVisible();
  expect(screen.getAllByText("Ada Mieter")).toHaveLength(2);
});
it("clears previous amounts when refresh fails instead of reporting stale balances", async () => {
  show(); await screen.findByText("Ada Mieter");
  deposits = () => Promise.reject(new ApiError(409, "Kautionskontobindung unvollständig"));
  fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
  expect(screen.queryByRole("table")).toBeNull();
  expect(await screen.findByRole("alert")).toHaveTextContent("Kautionskontobindung unvollständig");
  expect(screen.queryByText("1800.00 EUR")).toBeNull();
  expect(screen.queryByText("Keine Kautionen vorhanden.")).toBeNull();
});
it("does not replace a lease lookup error with a misleading report", async () => {
  leaseError = new Error("Verträge nicht verfügbar"); show();
  expect(await screen.findByRole("alert")).toHaveTextContent("Verträge nicht verfügbar");
  expect(screen.queryByRole("table")).toBeNull();
});
