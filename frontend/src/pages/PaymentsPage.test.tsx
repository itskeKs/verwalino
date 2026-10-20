import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { PaymentsPage } from "./PaymentsPage";

const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
beforeEach(() => {
  const fixtures: Record<string, unknown[]> = {
    "/api/payments": [],
    "/api/bank-accounts": [{ id: "deposit-account", property_id: "property", label: "Kautionskonto", account_type: "KAUTION" }],
    "/api/properties": [{ id: "property", name: "Testhaus" }],
    "/api/leases": [{ id: "lease", tenant_profile_id: "tenant", unit_id: "unit", valid_from: "2026-01-01", valid_to: null }],
    "/api/tenant-profiles": [{ id: "tenant", person_id: "person" }],
    "/api/owner-profiles": [{ id: "owner", person_id: "person" }],
    "/api/persons": [{ id: "person", first_name: "Ada", last_name: "Test" }],
  };
  vi.stubGlobal("fetch", vi.fn(async (input: string, options?: RequestInit) => {
    if (options?.method && options.method !== "GET") throw new Error("No mutation expected");
    if (input in fixtures) return json(fixtures[input]);
    throw new Error(`Unexpected URL: ${input}`);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function openCreate() {
  render(<MemoryRouter><PaymentsPage /></MemoryRouter>);
  await screen.findByText("Keine Zahlungen vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Zahlung" }));
}
function expectExplicitSelect(labelText: string) {
  const label = Array.from(document.querySelectorAll("label")).find(item => item.firstChild?.textContent?.trim() === labelText);
  const select = label?.querySelector("select");
  expect(select).toHaveAttribute("aria-label", labelText);
  expect(screen.getByLabelText(labelText, { exact: true })).toBe(select);
  expect(select).toHaveAccessibleName(labelText);
  return select!;
}

it.each(["Bankkonto *", "Zahlungstyp *", "Richtung *", "Mieter", "Mietvertrag"])("explicitly names %s for exact browser label lookup", async label => {
  await openCreate();
  expectExplicitSelect(label);
});
it("explicitly names Eigentuemer after switching to an owner payment type", async () => {
  await openCreate();
  const type = Array.from(document.querySelectorAll("label")).find(item => item.firstChild?.textContent?.trim() === "Zahlungstyp *")!.querySelector("select")!;
  fireEvent.change(type, { target: { value: "HAUSGELD_VORSCHUSS" } });
  expectExplicitSelect("Eigentuemer");
});
it.each(["IN", "OUT"])("supports KAUTION %s through the existing payment fields", async direction => {
  await openCreate();
  fireEvent.change(expectExplicitSelect("Bankkonto *"), { target: { value: "deposit-account" } });
  fireEvent.change(expectExplicitSelect("Zahlungstyp *"), { target: { value: "KAUTION" } });
  fireEvent.change(expectExplicitSelect("Richtung *"), { target: { value: direction } });
  fireEvent.change(expectExplicitSelect("Mieter"), { target: { value: "tenant" } });
  fireEvent.change(expectExplicitSelect("Mietvertrag"), { target: { value: "lease" } });
  expect(screen.getByLabelText("Bankkonto *", { exact: true })).toHaveValue("deposit-account");
  expect(screen.getByLabelText("Zahlungstyp *", { exact: true })).toHaveValue("KAUTION");
  expect(screen.getByLabelText("Richtung *", { exact: true })).toHaveValue(direction);
  expect(screen.getByLabelText("Mieter", { exact: true })).toHaveValue("tenant");
  expect(screen.getByLabelText("Mietvertrag", { exact: true })).toHaveValue("lease");
  expect(screen.getByLabelText("Betrag *", { exact: true })).toBeRequired();
  expect(screen.getByLabelText("Wertstellungsdatum *", { exact: true })).toBeRequired();
});
