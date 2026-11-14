import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { BankAccountsPage } from "./BankAccountsPage";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
it("keeps the account table mounted while a confirmed edit refreshes", async () => {
  let finish!: (value: Response) => void;
  let saved = false;
  const account = { id: "konto", label: "Hauskonto", property_name: "Testhaus", account_type: "LAUFEND" };
  vi.stubGlobal("fetch", vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === "PATCH") { saved = true; return json({ ...account, label: "Neues Hauskonto" }); }
    if (url.startsWith("/api/bank-accounts")) return saved ? new Promise<Response>(resolve => { finish = resolve; }) : json([account]);
    return json({ items: [], next_cursor: null });
  }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role: "CLERK" }, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout: vi.fn() }}><MemoryRouter><BankAccountsPage /></MemoryRouter></AuthCtx.Provider>);
  await screen.findByText("Hauskonto");
  const table = screen.getByRole("table");
  fireEvent.click(screen.getByRole("button", { name: "Bankkonto bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Bezeichnung", { exact: true }), { target: { value: "Neues Hauskonto" } });
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Bankkonto gespeichert.");
  expect(table).toBeInTheDocument();
  await act(async () => finish(json([{ ...account, label: "Neues Hauskonto" }])));
  expect(await screen.findByText("Neues Hauskonto")).toBeVisible();
});
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
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role: "OWNER_ADMIN" }, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout: vi.fn() }}><MemoryRouter><BankAccountsPage /></MemoryRouter></AuthCtx.Provider>);
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

it("explicitly names the account type select for exact browser label lookup", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (input.startsWith("/api/bank-accounts")) return json([]);
    if (input.startsWith("/api/properties/page")) return json({ items: [], next_cursor: null });
    throw new Error(`Unexpected URL: ${input}`);
  }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role: "OWNER_ADMIN" }, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout: vi.fn() }}><MemoryRouter><BankAccountsPage /></MemoryRouter></AuthCtx.Provider>);
  fireEvent.click(screen.getByRole("button", { name: "+ Neues Bankkonto" }));
  const label = Array.from(document.querySelectorAll("label")).find(item => item.firstChild?.textContent?.trim() === "Kontotyp *");
  const select = label?.querySelector("select");
  expect(select).toHaveAttribute("aria-label", "Kontotyp *");
  expect(screen.getByLabelText("Kontotyp *", { exact: true })).toBe(select);
  expect(select).toHaveAccessibleName("Kontotyp *");
  fireEvent.change(select!, { target: { value: "KAUTION" } });
  expect(select).toHaveValue("KAUTION");
  fireEvent.change(select!, { target: { value: "LAUFEND" } });
  expect(select).toHaveValue("LAUFEND");
  await screen.findByText("Keine Bankkonten vorhanden.");
});

it.each(["OWNER_ADMIN", "CLERK", "READ_ONLY"])("verlinkt Kontoumsätze und schützt den Importlink für %s", async role => {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    if (input.startsWith("/api/bank-accounts")) return json([{ id: "konto", label: "Hauskonto", account_type: "LAUFEND", property_name: "Testhaus" }]);
    if (input.startsWith("/api/properties/page")) return json({ items: [], next_cursor: null });
    throw new Error(`Unexpected URL: ${input}`);
  }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout: vi.fn() }}><MemoryRouter><BankAccountsPage /></MemoryRouter></AuthCtx.Provider>);
  expect(await screen.findByRole("link", { name: "Umsätze" })).toHaveAttribute("href", "/bankkonten/konto/umsaetze");
  if (role === "READ_ONLY") {
    expect(screen.queryByRole("link", { name: "Kontoauszug importieren" })).not.toBeInTheDocument();
  } else {
    expect(screen.getByRole("link", { name: "Kontoauszug importieren" })).toHaveAttribute("href", "/bankkonten/konto/umsaetze?import=1");
  }
});


it("keeps the canonical account label during a second edit opened before refresh completes", async () => {
  let refresh!: (value: Response) => void;
  const account = { id: 'konto', label: 'Old label', property_name: 'Testhaus', account_type: 'LAUFEND', bank_name: null, iban: null, bic: null };
  const canonical = { ...account, label: 'Confirmed new label' };
  const changes: unknown[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === 'PATCH') { changes.push(JSON.parse(options.body as string)); return json(canonical); }
    if (url.startsWith('/api/bank-accounts')) return changes.length ? new Promise<Response>(resolve => { refresh = resolve; }) : json([account]);
    return json({ items: [], next_cursor: null });
  }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: 'u', organization_id: 'o', role: 'CLERK' }, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout: vi.fn() }}><MemoryRouter><BankAccountsPage /></MemoryRouter></AuthCtx.Provider>);
  fireEvent.click(await screen.findByRole('button', { name: 'Bankkonto bearbeiten' }));
  fireEvent.change(screen.getByLabelText('Bezeichnung', { exact: true }), { target: { value: canonical.label } });
  fireEvent.click(screen.getByRole('button', { name: 'Änderungen speichern' }));
  await waitFor(() => expect(refresh).toBeTypeOf('function'));
  fireEvent.click(screen.getByRole('button', { name: 'Bankkonto bearbeiten' }));
  await act(async () => refresh(json([canonical])));
  fireEvent.change(screen.getByLabelText('Bank', { exact: true }), { target: { value: 'New bank' } });
  fireEvent.click(screen.getByRole('button', { name: 'Änderungen speichern' }));
  await waitFor(() => expect(changes).toHaveLength(2));
  expect(changes).toEqual([{ label: canonical.label }, { bank_name: 'New bank' }]);
});
