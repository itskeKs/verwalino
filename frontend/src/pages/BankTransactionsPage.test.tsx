import { afterEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthCtx, type AuthContextValue } from "../auth/useAuth";
import { BankTransactionsPage } from "./BankTransactionsPage";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const transaction = { id: "tx", bank_account_id: "a", booking_date: "2026-01-02", value_date: "2026-01-03", amount: "-1234567890123456.78", counterparty_name: "Muster GmbH", counterparty_iban: "DE123", purpose: "Heizung", status: "NEU" };
const preview = { count: 3, new_count: 3, duplicate_count: 0, imported_count: 0, total_amount: "12.30", new_total_amount: "12.30", date_from: "2026-01-02", date_to: "2026-01-03", transactions: [{ ...transaction, import_hash: "hash", is_duplicate: false }], preview_truncated: true };
const auth = (role = "OWNER_ADMIN"): AuthContextValue => ({ loading: false, session: { role, user_id: "user", organization_id: "org" }, login: vi.fn(), logout: vi.fn() });
function setup(handler?: (url: string, options?: RequestInit) => Promise<Response>, role = "OWNER_ADMIN", initial = "/bankkonten/a/umsaetze?import=1") {
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (handler) return handler(url, options);
    return fallback(url);
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<AuthCtx.Provider value={auth(role)}><MemoryRouter initialEntries={[initial]}><Link to="/bankkonten/b/umsaetze?import=1">Anderes Konto</Link><Routes><Route path="/bankkonten/:id/umsaetze" element={<BankTransactionsPage />} /></Routes></MemoryRouter></AuthCtx.Provider>);
  return fetchMock;
}
function fallback(url: string) {
  return Promise.resolve(json(url.includes("/transactions") ? { items: [transaction], next_offset: null } : { id: url.split("/").pop(), label: "Hauskonto", iban: "DE123" }));
}
const choose = (name = "umsatz.csv") => fireEvent.change(screen.getByLabelText("Kontoauszug"), { target: { files: [new File(["csv"], name, { type: "text/csv" })] } });
afterEach(() => vi.unstubAllGlobals());

it("lädt Konto und exakte deutsche Beträge, paginiert und setzt Filter auf Seite 0 zurück", async () => {
  const fetchMock = setup(async url => {
    if (!url.includes("/transactions")) return fallback(url);
    const params = new URL(url, "http://test").searchParams;
    return json({ items: [{ ...transaction, purpose: params.get("offset") === "50" ? "Seite zwei" : "Heizung" }], next_offset: params.get("offset") === "50" ? null : 50 });
  });
  expect(await screen.findByText("Heizung")).toBeInTheDocument();
  expect(screen.getByText("-1.234.567.890.123.456,78 €")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /Hauskonto/ })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
  expect(await screen.findByText("Seite zwei")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "ZUGEORDNET" } });
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes("status=ZUGEORDNET") && url.includes("offset=0") && url.includes("limit=50"))).toBe(true));
  expect(await screen.findByText("Heizung")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Zurück" })).toBeDisabled();
});

it("sendet nur Vorschau bis zur expliziten Übernahme und meldet wiederholte Importe mit 0 neuen Umsätzen", async () => {
  let writes = 0;
  const fetchMock = setup(async (url) => {
    if (url.includes("/imports")) return json(url.endsWith("dry_run=true") ? { ...preview, new_count: writes ? 0 : 3, duplicate_count: writes ? 3 : 0 } : { ...preview, imported_count: writes++ ? 0 : 3 });
    return fallback(url);
  });
  await screen.findByText("Heizung");
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau erstellen" }));
  await screen.findByRole("heading", { name: "Importvorschau" });
  expect(writes).toBe(0);
  const call = fetchMock.mock.calls.find(([url]) => url.endsWith("dry_run=true"))!;
  expect(call[0]).toBe("/api/bank-accounts/a/imports?dry_run=true");
  expect(call[1]).toMatchObject({ method: "POST", credentials: "include" });
  expect(call[1]?.body).toBeInstanceOf(FormData);
  expect((call[1]?.body as FormData).get("file")).toBeInstanceOf(File);
  expect(screen.getByText(/Vorschau.*gekürzt/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
  await screen.findByText("3 neue Umsätze importiert.");
  expect(fetchMock.mock.calls.filter(([url]) => url.includes("/transactions")).length).toBeGreaterThan(1);
  fireEvent.click(screen.getByRole("button", { name: "Vorschau erstellen" }));
  await screen.findByRole("heading", { name: "Importvorschau" });
  fireEvent.click(screen.getByRole("button", { name: "Übernehmen" }));
  await screen.findByText("0 neue Umsätze importiert.");
});

it("verwirft eine Vorschau nach Dateiwechsel und schützt laufende Requests vor Doppelklicks", async () => {
  let resolve!: (response: Response) => void;
  const fetchMock = setup(url => url.includes("/imports") ? new Promise<Response>(r => { resolve = r; }) : fallback(url));
  await screen.findByText("Heizung");
  choose();
  const button = screen.getByRole("button", { name: "Vorschau erstellen" });
  fireEvent.click(button);
  fireEvent.click(button);
  expect(fetchMock.mock.calls.filter(([url]) => url.includes("/imports"))).toHaveLength(1);
  choose("neu.csv");
  await act(async () => resolve(json(preview)));
  expect(screen.queryByRole("heading", { name: "Importvorschau" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Übernehmen" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau erstellen" }));
  await act(async () => resolve(json(preview)));
  await screen.findByRole("heading", { name: "Importvorschau" });
  choose("dritte.csv");
  expect(screen.queryByRole("button", { name: "Übernehmen" })).not.toBeInTheDocument();
});

it("zeigt API- und Netzwerkfehler beim Import und bietet keine Übernahme ohne Vorschau", async () => {
  let attempts = 0;
  setup(async url => {
    if (!url.includes("/imports")) return fallback(url);
    if (attempts++ === 0) return json({ detail: "CSV ungültig" }, 422);
    throw new TypeError("Netzwerk nicht erreichbar");
  });
  await screen.findByText("Heizung");
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau erstellen" }));
  await screen.findByText("CSV ungültig");
  expect(screen.queryByRole("button", { name: "Übernehmen" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau erstellen" }));
  await screen.findByText("Netzwerk nicht erreichbar");
});

it("zeigt Lade-, Fehler- und Leerzustände der Liste", async () => {
  let resolve!: (response: Response) => void;
  let attempts = 0;
  setup(url => {
    if (!url.includes("/transactions")) return fallback(url);
    if (attempts++ === 0) return new Promise<Response>(r => { resolve = r; });
    return Promise.resolve(json({ items: [], next_offset: null }));
  });
  expect(screen.getByText("Lade Umsätze…")).toBeInTheDocument();
  await act(async () => resolve(json({ detail: "Liste nicht verfügbar" }, 503)));
  await screen.findByText("Liste nicht verfügbar");
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));
  await screen.findByText("Keine Umsätze vorhanden.");
});

it("lässt READ_ONLY Umsätze sehen, aber auch bei import=1 keinen Import auslösen", async () => {
  setup(undefined, "READ_ONLY");
  await screen.findByText("Heizung");
  expect(screen.queryByLabelText("Kontoauszug")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Kontoauszug importieren" })).not.toBeInTheDocument();
});

it("übernimmt beim Kontowechsel weder alte Liste noch alte Importvorschau", async () => {
  let resolveList!: (response: Response) => void;
  let resolveImport!: (response: Response) => void;
  setup(url => {
    if (url.includes("/a/imports")) return new Promise<Response>(r => { resolveImport = r; });
    if (url.includes("/a/transactions")) return new Promise<Response>(r => { resolveList = r; });
    if (url.includes("/b/transactions")) return Promise.resolve(json({ items: [], next_offset: null }));
    return fallback(url);
  });
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau erstellen" }));
  fireEvent.click(screen.getByRole("link", { name: "Anderes Konto" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  await act(async () => { resolveList(json({ items: [transaction], next_offset: null })); resolveImport(json(preview)); });
  expect(screen.queryByText("Heizung")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Importvorschau" })).not.toBeInTheDocument();
});

it("sperrt doppelte Übernahme und zeigt Übernahmefehler ohne Erfolgsmeldung", async () => {
  let resolve!: (response: Response) => void;
  const fetchMock = setup(url => {
    if (url.endsWith("dry_run=true")) return Promise.resolve(json(preview));
    if (url.endsWith("dry_run=false")) return new Promise<Response>(r => { resolve = r; });
    return fallback(url);
  });
  await screen.findByText("Heizung");
  choose();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau erstellen" }));
  const takeOver = await screen.findByRole("button", { name: "Übernehmen" });
  fireEvent.click(takeOver);
  fireEvent.click(takeOver);
  expect(takeOver).toBeDisabled();
  expect(screen.getByLabelText("Kontoauszug")).toBeDisabled();
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith("dry_run=false"))).toHaveLength(1);
  await act(async () => resolve(json({ detail: "Import fehlgeschlagen" }, 503)));
  await screen.findByText("Import fehlgeschlagen");
  expect(screen.queryByText(/neue Umsätze importiert/)).not.toBeInTheDocument();
  expect(takeOver).toBeEnabled();
});

it("beschriftet den Statusfilter eindeutig ohne Optionstexte im Label", async () => {
  setup();
  await screen.findByText("Heizung");
  const select = screen.getByRole("combobox") as HTMLSelectElement;
  expect(select.labels?.[0].textContent).toBe("Status");
});

it("bietet alle vom Import unterstützten Dateiformate an", async () => {
  setup();
  await screen.findByText("Heizung");
  const input = screen.getByLabelText("Kontoauszug") as HTMLInputElement;
  for (const extension of [".csv", ".xml", ".zip"]) expect(input.accept.split(",")).toContain(extension);
});

it("öffnet den Import erst auf Wunsch, wenn import=1 nicht gesetzt ist", async () => {
  setup(undefined, "CLERK", "/bankkonten/a/umsaetze");
  await screen.findByText("Heizung");
  expect(screen.queryByLabelText("Kontoauszug")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Kontoauszug importieren" }));
  expect(screen.getByLabelText("Kontoauszug")).toBeInTheDocument();
});
