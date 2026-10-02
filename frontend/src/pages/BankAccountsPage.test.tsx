import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BankAccountsPage } from "./BankAccountsPage";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
it("clears incompatible scope when switching between deposit and reserve accounts", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = new URL(input, "http://test");
    if (url.pathname === "/api/bank-accounts") return json([]);
    if (url.pathname === "/api/properties/page") return json({ items: [{ id: "property", name: "Testhaus", management_type: "MISCH" }], next_cursor: null });
    if (url.pathname === "/api/accounting-scopes/page") {
      const scopes = ["WEG", "MIET"].filter(kind => !url.searchParams.get("scope_type") || url.searchParams.get("scope_type") === kind);
      return json({ items: scopes.map(kind => ({ id: kind, property_id: "property", label: `Bereich ${kind}`, scope_type: kind, valid_from: "2025-01-01", valid_to: null })), next_cursor: null });
    }
    throw new Error(`Unexpected URL: ${input}`);
  }));
  render(<MemoryRouter><BankAccountsPage /></MemoryRouter>);
  fireEvent.click(screen.getByRole("button", { name: "+ Neues Bankkonto" }));
  await screen.findAllByRole("option", { name: /Testhaus/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Objekt *" }), { target: { value: "property" } });
  await screen.findByRole("option", { name: /Bereich WEG/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "WEG" } });
  fireEvent.change(screen.getByLabelText(/Kontotyp/), { target: { value: "KAUTION" } });
  await screen.findByRole("option", { name: /Bereich MIET/ });
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("");
  expect(screen.queryByRole("option", { name: /Bereich WEG/ })).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "MIET" } });
  fireEvent.change(screen.getByLabelText(/Kontotyp/), { target: { value: "RUECKLAGE" } });
  await screen.findByRole("option", { name: /Bereich WEG/ });
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("");
  expect(screen.queryByRole("option", { name: /Bereich MIET/ })).toBeNull();
});
