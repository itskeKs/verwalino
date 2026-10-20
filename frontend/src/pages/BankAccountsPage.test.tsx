import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
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
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role: "OWNER_ADMIN" }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><BankAccountsPage /></MemoryRouter></AuthCtx.Provider>);
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

it.each(["OWNER_ADMIN", "CLERK", "READ_ONLY"])("verlinkt Kontoumsätze und schützt den Importlink für %s", async role => {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (input.startsWith("/api/bank-accounts")) return json([{ id: "konto", label: "Hauskonto", account_type: "LAUFEND", property_name: "Testhaus" }]);
    if (input.startsWith("/api/properties/page")) return json({ items: [], next_cursor: null });
    throw new Error(`Unexpected URL: ${input}`);
  }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><BankAccountsPage /></MemoryRouter></AuthCtx.Provider>);
  expect(await screen.findByRole("link", { name: "Umsätze" })).toHaveAttribute("href", "/bankkonten/konto/umsaetze");
  if (role === "READ_ONLY") {
    expect(screen.queryByRole("link", { name: "Kontoauszug importieren" })).not.toBeInTheDocument();
  } else {
    expect(screen.getByRole("link", { name: "Kontoauszug importieren" })).toHaveAttribute("href", "/bankkonten/konto/umsaetze?import=1");
  }
});
