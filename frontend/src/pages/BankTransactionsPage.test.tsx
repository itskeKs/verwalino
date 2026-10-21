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
  return Promise.resolve(json(isTransactionList(url) ? { items: [transaction], next_offset: null } : { id: url.split("/").pop(), label: "Hauskonto", iban: "DE123" }));
}
const isTransactionList = (url: string) => url.includes("/bank-transactions?") || url.includes("/transactions?");
const choose = (name = "umsatz.csv") => fireEvent.change(screen.getByLabelText("Kontoauszug"), { target: { files: [new File(["csv"], name, { type: "text/csv" })] } });
afterEach(() => vi.unstubAllGlobals());

it("lädt Konto und exakte deutsche Beträge, paginiert und setzt Filter auf Seite 0 zurück", async () => {
  const fetchMock = setup(async url => {
    if (!isTransactionList(url)) return fallback(url);
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
  expect(fetchMock.mock.calls.filter(([url]) => isTransactionList(url)).length).toBeGreaterThan(1);
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
    if (!isTransactionList(url)) return fallback(url);
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
    if ((url.includes("/a/transactions") || isTransactionList(url) && url.includes("bank_account_id=a"))) return new Promise<Response>(r => { resolveList = r; });
    if ((url.includes("/b/transactions") || isTransactionList(url) && url.includes("bank_account_id=b"))) return Promise.resolve(json({ items: [], next_offset: null }));
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

const cold = { payment_type: "KALTMIETE", amount: "700.00", open_amount: "700.00", period_year: 2026, period_month: 1, lease_agreement_id: "lease", tenant_profile_id: "tenant", owner_profile_id: null };
const utilities = { ...cold, payment_type: "NK_VORAUSZAHLUNG", amount: "150.00", open_amount: "150.00" };
const suggestion = { target_name: "Anna Muster", confidence: "HIGH", lines: [cold, utilities], reasons: ["IBAN und Warmmiete passen"], requires_review: false, unallocated_amount: "0.00" };
const warm = { ...transaction, amount: "850.00", suggestions: [suggestion] };
const targets: Record<string, unknown> = {
  "/api/units": [{ id: "unit", property_id: "property", number: "1" }, { id: "foreign-unit", property_id: "foreign", number: "Fremd" }],
  "/api/leases": [{ id: "lease", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-01-01", valid_to: null }, { id: "foreign-lease", unit_id: "foreign-unit", tenant_profile_id: "tenant", valid_from: "2025-01-01", valid_to: null }],
  "/api/tenant-profiles": [{ id: "tenant", person_id: "person" }],
  "/api/owner-profiles": [{ id: "owner", person_id: "person" }],
  "/api/persons": [{ id: "person", first_name: "Anna", last_name: "Muster" }],
  "/api/ownership": [{ id: "ownership", unit_id: "unit", owner_profile_id: "owner", valid_from: "2025-01-01", valid_to: null }],
};
function assignmentSetup(item: unknown = warm, role = "OWNER_ADMIN", mutation?: (url: string, options?: RequestInit) => Promise<Response>) {
  let processed = false;
  return setup(async (url, options) => {
    if (options?.method === "POST" && /\/(assign|ignore)$/.test(url)) {
      if (mutation) return mutation(url, options);
      processed = true;
      return json({ id: "tx", status: url.endsWith("/ignore") ? "IGNORIERT" : "ZUGEORDNET", payments: [] });
    }
    if (isTransactionList(url)) return json({ items: processed ? [] : [item], next_offset: null });
    const path = url.split("?")[0];
    if (path in targets) return json(targets[path]);
    return json({ id: "a", property_id: "property", label: "Hauskonto", iban: "DE123" });
  }, role, "/bankkonten/a/umsaetze");
}
const posts = (fetchMock: ReturnType<typeof assignmentSetup>) => fetchMock.mock.calls.filter(([, options]) => options?.method === "POST");

it("übernimmt Warmmiete über die globale Kontoliste mit exakt 700 KALT und 150 NK ohne Vorschlagsmetadaten", async () => {
  const fetchMock = assignmentSetup();
  await screen.findByText("IBAN und Warmmiete passen");
  expect(screen.getByText(/HIGH/)).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.startsWith("/api/bank-transactions?") && url.includes("bank_account_id=a") && url.includes("status=NEU"))).toBe(true);
  expect(posts(fetchMock)).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Vorschlag übernehmen" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  expect(posts(fetchMock)).toHaveLength(1);
  expect(posts(fetchMock)[0][0]).toBe("/api/bank-transactions/tx/assign");
  expect(JSON.parse(posts(fetchMock)[0][1]!.body as string)).toEqual({ lines: [cold, utilities].map(({ payment_type, amount, period_year, period_month, lease_agreement_id, tenant_profile_id }) => ({ payment_type, amount, period_year, period_month, lease_agreement_id, tenant_profile_id })) });
});

it.each([
  { ...suggestion, confidence: "LOW" },
  { ...suggestion, requires_review: true },
  { ...suggestion, unallocated_amount: "1.00" },
  { ...suggestion, lines: [{ ...cold, amount: null }, { ...utilities, amount: null }], unallocated_amount: "600.00" },
  { ...suggestion, lines: [{ ...cold, amount: "850.001" }] },
  { ...suggestion, lines: [] },
])("öffnet unvollständige/prüfpflichtige Vorschläge ohne POST im Dialog ($confidence)", async candidate => {
  const fetchMock = assignmentSetup({ ...warm, suggestions: [candidate] });
  fireEvent.click(await screen.findByRole("button", { name: "Vorschlag übernehmen" }));
  expect(await screen.findByRole("dialog", { name: "Umsatz aufteilen" })).toBeInTheDocument();
  expect(posts(fetchMock)).toHaveLength(0);
});

it("erzwingt bei Teilzahlung 600 die exakte positive Aufteilung und behält Eingaben bei Fehlern", async () => {
  const fetchMock = assignmentSetup({ ...warm, amount: "600.00", suggestions: [{ ...suggestion, lines: [{ ...cold, amount: null }, { ...utilities, amount: null }], requires_review: true, unallocated_amount: "600.00" }] });
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  await screen.findByRole("dialog");
  await waitFor(() => expect(screen.getAllByLabelText("Mietvertrag")[0]).toHaveValue("lease"));
  const amounts = screen.getAllByLabelText("Betrag (EUR)");
  fireEvent.change(amounts[0], { target: { value: "500,01" } });
  fireEvent.change(amounts[1], { target: { value: "100,00" } });
  fireEvent.click(screen.getByRole("button", { name: "Zuordnung buchen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/Summe/);
  expect(posts(fetchMock)).toHaveLength(0);
  fireEvent.change(amounts[0], { target: { value: "500,00" } });
  fireEvent.click(screen.getByRole("button", { name: "Zuordnung buchen" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  expect(JSON.parse(posts(fetchMock)[0][1]!.body as string).lines.map((line: { amount: string }) => line.amount)).toEqual(["500.00", "100.00"]);
});

it("ermöglicht manuelle Miet- und Eigentümerziele ohne Vorschlag nur im Bankobjekt", async () => {
  const fetchMock = assignmentSetup({ ...warm, suggestions: [] });
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  const lease = await screen.findByLabelText("Mietvertrag");
  await screen.findAllByRole("option", { name: /Anna Muster.*2025-01-01/ });
  expect(screen.queryByRole("option", { name: /Fremd/ })).not.toBeInTheDocument();
  fireEvent.change(lease, { target: { value: "lease" } });
  fireEvent.change(screen.getByLabelText("Betrag (EUR)"), { target: { value: "700,00" } });
  fireEvent.click(screen.getByRole("button", { name: "Zeile hinzufügen" }));
  fireEvent.change(screen.getAllByLabelText("Zahlungsart")[1], { target: { value: "HAUSGELD_VORSCHUSS" } });
  fireEvent.change(screen.getByLabelText("Eigentümer"), { target: { value: "owner" } });
  fireEvent.change(screen.getAllByLabelText("Betrag (EUR)")[1], { target: { value: "150,00" } });
  fireEvent.click(screen.getByRole("button", { name: "Zuordnung buchen" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  const lines = JSON.parse(posts(fetchMock)[0][1]!.body as string).lines;
  expect(lines[0]).toMatchObject({ lease_agreement_id: "lease", tenant_profile_id: "tenant", amount: "700.00" });
  expect(lines[1]).toMatchObject({ owner_profile_id: "owner", amount: "150.00" });
  expect(lines[1]).not.toHaveProperty("tenant_profile_id");
  expect(lines[1]).not.toHaveProperty("lease_agreement_id");
});

it("zeigt negative Bankumsätze vorzeichenbehaftet, sendet aber positive Splitbeträge ohne direction", async () => {
  const fetchMock = assignmentSetup({ ...warm, amount: "-850.00" });
  await screen.findByText("-850,00 €");
  fireEvent.click(screen.getByRole("button", { name: "Vorschlag übernehmen" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  const lines = JSON.parse(posts(fetchMock)[0][1]!.body as string).lines;
  expect(lines.map((line: { amount: string }) => line.amount)).toEqual(["700.00", "150.00"]);
  expect(lines[0]).not.toHaveProperty("direction");
});

it.each(["READ_ONLY", "TENANT"]) ("versteckt alle Mutationsbuttons für %s", async role => {
  const fetchMock = assignmentSetup(warm, role);
  await screen.findByText("IBAN und Warmmiete passen");
  for (const name of ["Vorschlag übernehmen", "Aufteilen", "Ignorieren"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  expect(posts(fetchMock)).toHaveLength(0);
});

it.each(["ZUGEORDNET", "IGNORIERT"])("bietet für verarbeitete Umsätze (%s) keine Mutationen", async status => {
  assignmentSetup({ ...warm, status });
  await screen.findByText("Heizung");
  for (const name of ["Vorschlag übernehmen", "Aufteilen", "Ignorieren"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
});

it("ignoriert ausdrücklich und lädt denselben Statusfilter neu", async () => {
  const fetchMock = assignmentSetup();
  fireEvent.click(await screen.findByRole("button", { name: "Ignorieren" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  expect(posts(fetchMock)[0][0]).toBe("/api/bank-transactions/tx/ignore");
  expect(fetchMock.mock.calls.filter(([url]) => url.includes("status=NEU"))).toHaveLength(2);
});

it("sperrt doppelte Buchung synchron während eines laufenden Requests", async () => {
  let resolve!: (response: Response) => void;
  const fetchMock = assignmentSetup(warm, "CLERK", () => new Promise(r => { resolve = r; }));
  const button = await screen.findByRole("button", { name: "Vorschlag übernehmen" });
  fireEvent.click(button); fireEvent.click(button);
  expect(posts(fetchMock)).toHaveLength(1);
  expect(button).toBeDisabled();
  expect(screen.getByRole("button", { name: "Ignorieren" })).toBeDisabled();
  await act(async () => resolve(json({ detail: "Keine Buchungsberechtigung" }, 403)));
  expect(await screen.findByRole("alert")).toHaveTextContent("Keine Buchungsberechtigung");
  expect(button).toBeEnabled();
});

it.each([409, 422])("zeigt Buchungsfehler %s im Dialog ohne Eingabeverlust oder Erfolg", async status => {
  assignmentSetup(warm, "OWNER_ADMIN", async () => json({ detail: status === 409 ? "Bereits verarbeitet" : "Ungültige Zuordnung" }, status));
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  await screen.findAllByRole("option", { name: /Anna Muster.*2025-01-01/ });
  fireEvent.click(screen.getByRole("button", { name: "Zuordnung buchen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(status === 409 ? "Bereits verarbeitet" : "Ungültige Zuordnung");
  expect(screen.getAllByLabelText("Betrag (EUR)")[0]).toHaveValue("700.00");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
  expect(screen.queryByText("Umsatz zugeordnet.")).not.toBeInTheDocument();
});

it("meldet Netzwerkfehler beim Ignorieren sichtbar und erlaubt Wiederholung", async () => {
  assignmentSetup(warm, "OWNER_ADMIN", async () => { throw new TypeError("Netzwerk nicht erreichbar"); });
  fireEvent.click(await screen.findByRole("button", { name: "Ignorieren" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Netzwerk nicht erreichbar");
  expect(screen.getByRole("button", { name: "Ignorieren" })).toBeEnabled();
});

it("verlangt bei mehreren passenden Vorschlägen explizite Auswahl im Dialog", async () => {
  const fetchMock = assignmentSetup({ ...warm, suggestions: [suggestion, { ...suggestion, target_name: "Andere Zuordnung" }] });
  fireEvent.click(await screen.findByRole("button", { name: "Vorschlag übernehmen" }));
  await screen.findByRole("dialog", { name: "Umsatz aufteilen" });
  expect(posts(fetchMock)).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Vorschlag 1 laden: Anna Muster" }));
  await waitFor(() => expect(screen.getAllByLabelText("Mietvertrag")[0]).toHaveValue("lease"));
  fireEvent.click(screen.getByRole("button", { name: "Zuordnung buchen" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  expect(posts(fetchMock)).toHaveLength(1);
});

it.each(["-850.00", "0", "850.001", "1e3", "1000000000000.00", "NaN"])("verhindert ungültigen Splitbetrag %s vor dem POST", async amount => {
  const fetchMock = assignmentSetup();
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  await screen.findAllByRole("option", { name: /Anna Muster.*2025-01-01/ });
  fireEvent.change(screen.getAllByLabelText("Betrag (EUR)")[0], { target: { value: amount } });
  fireEvent.click(screen.getByRole("button", { name: "Zuordnung buchen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/positive Beträge/);
  expect(posts(fetchMock)).toHaveLength(0);
});

it("entfernt Splitzeilen ohne versteckte Restbuchung und prüft Perioden", async () => {
  const fetchMock = assignmentSetup();
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  await screen.findAllByRole("option", { name: /Anna Muster.*2025-01-01/ });
  fireEvent.click(screen.getAllByRole("button", { name: "Zeile entfernen" })[1]);
  expect(screen.getAllByLabelText("Betrag (EUR)")).toHaveLength(1);
  fireEvent.change(screen.getByLabelText("Betrag (EUR)"), { target: { value: "850,00" } });
  fireEvent.change(screen.getByLabelText("Periode Monat"), { target: { value: "13" } });
  fireEvent.click(screen.getByRole("button", { name: "Zuordnung buchen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/Periode/);
  expect(posts(fetchMock)).toHaveLength(0);
  fireEvent.change(screen.getByLabelText("Periode Monat"), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: "Zuordnung buchen" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  expect(JSON.parse(posts(fetchMock)[0][1]!.body as string).lines).toEqual([{ payment_type: "KALTMIETE", amount: "850.00", period_year: 2026, period_month: 2, lease_agreement_id: "lease", tenant_profile_id: "tenant" }]);
});

it("meldet Zielladefehler und erlaubt Wiederholung ohne rohe UUID-Eingabe", async () => {
  let failed = false;
  const fetchMock = setup(async (url) => {
    if (isTransactionList(url)) return json({ items: [{ ...warm, suggestions: [] }], next_offset: null });
    const path = url.split("?")[0];
    if (path === "/api/leases" && !failed) { failed = true; return json({ detail: "Ziele nicht verfügbar" }, 503); }
    if (path in targets) return json(targets[path]);
    return json({ id: "a", label: "Hauskonto", property_id: "property" });
  }, "OWNER_ADMIN", "/bankkonten/a/umsaetze");
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Ziele nicht verfügbar");
  expect(screen.getByRole("button", { name: "Zuordnung buchen" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Ziele erneut laden" }));
  await screen.findAllByRole("option", { name: /Anna Muster.*2025-01-01/ });
  expect(screen.getByRole("button", { name: "Zuordnung buchen" })).toBeEnabled();
  expect(posts(fetchMock)).toHaveLength(0);
});

it("übernimmt nach Kontowechsel keine alte Mutationsantwort", async () => {
  let resolve!: (response: Response) => void;
  setup(async (url, options) => {
    if (options?.method === "POST") return new Promise<Response>(r => { resolve = r; });
    if (isTransactionList(url)) return json({ items: url.includes("bank_account_id=b") ? [] : [warm], next_offset: null });
    return json({ id: "a", label: "Hauskonto", property_id: "property" });
  }, "OWNER_ADMIN", "/bankkonten/a/umsaetze");
  fireEvent.click(await screen.findByRole("button", { name: "Vorschlag übernehmen" }));
  fireEvent.click(screen.getByRole("link", { name: "Anderes Konto" }));
  await screen.findByText("Keine Umsätze vorhanden.");
  await act(async () => resolve(json({ id: "tx", status: "ZUGEORDNET", payments: [] })));
  expect(screen.queryByText("Umsatz zugeordnet.")).not.toBeInTheDocument();
});

it("lädt bei Alle explizit status=ALL und zeigt NEU, Zugeordnet und Ignoriert ohne NEU-Fallback", async () => {
  const allItems = [
    { ...transaction, id: "new", purpose: "Offene Warmmiete", status: "NEU" },
    { ...transaction, id: "assigned", purpose: "Verbuchte Warmmiete", status: "ZUGEORDNET" },
    { ...transaction, id: "ignored", purpose: "Ignorierter Umsatz", status: "IGNORIERT" },
  ];
  const fetchMock = setup(async url => {
    if (!isTransactionList(url)) return fallback(url);
    const status = new URL(url, "http://test").searchParams.get("status");
    return json({ items: status === "ALL" ? allItems : [allItems[0]], next_offset: null });
  });
  await screen.findByText("Offene Warmmiete");
  const filter = screen.getByLabelText("Status");
  expect(filter).toHaveValue("NEU");
  expect(screen.getByRole("option", { name: /^Alle$/ })).toHaveValue("");
  fireEvent.change(filter, { target: { value: "" } });
  expect(await screen.findByText("Verbuchte Warmmiete")).toBeInTheDocument();
  expect(screen.getByText("Ignorierter Umsatz")).toBeInTheDocument();
  expect(screen.getByText("Offene Warmmiete")).toBeInTheDocument();
  expect(filter).toHaveValue("");
  const lists = fetchMock.mock.calls.filter(([url]) => isTransactionList(url));
  expect(lists).toHaveLength(2);
  const query = new URL(lists[1][0], "http://test").searchParams;
  expect(query.get("status")).toBe("ALL");
  expect(query.get("bank_account_id")).toBe("a");
  expect(query.get("offset")).toBe("0");
  expect(query.get("limit")).toBe("50");
  fireEvent.change(filter, { target: { value: "NEU" } });
  await waitFor(() => expect(screen.queryByText("Verbuchte Warmmiete")).not.toBeInTheDocument());
  expect(await screen.findByText("Offene Warmmiete")).toBeInTheDocument();
  expect(new URL(fetchMock.mock.calls.filter(([url]) => isTransactionList(url)).at(-1)![0], "http://test").searchParams.get("status")).toBe("NEU");
});

it.each(["Mietvertrag", "Zahlungsart"])("beschriftet beide Split-Comboboxen exakt mit %s ohne Optionstexte", async label => {
  assignmentSetup();
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  await screen.findAllByRole("option", { name: /Anna Muster.*2025-01-01/ });
  const selects = screen.getAllByRole("combobox", { name: new RegExp(`^${label}$`) });
  expect(selects).toHaveLength(2);
  for (const select of selects) {
    expect(select).toHaveAccessibleName(label);
    expect(select).toHaveAttribute("aria-label", label);
  }
});

it("beschriftet die Eigentümer-Combobox exakt ohne Optionstexte", async () => {
  assignmentSetup({ ...warm, suggestions: [{ ...suggestion, lines: [{ ...cold, payment_type: "HAUSGELD_VORSCHUSS", amount: "850.00", lease_agreement_id: null, tenant_profile_id: null, owner_profile_id: "owner" }] }] });
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  await screen.findByRole("option", { name: /Anna Muster.*Einheit/ });
  const select = screen.getByRole("combobox", { name: /^Eigentümer$/ });
  expect(select).toHaveAccessibleName("Eigentümer");
  expect(select).toHaveAttribute("aria-label", "Eigentümer");
});

it("beschriftet die Zielart-Combobox exakt ohne Mietvertrags- und Eigentümeroptionen im Namen", async () => {
  assignmentSetup({ ...warm, suggestions: [{ ...suggestion, lines: [{ ...cold, payment_type: "NACHZAHLUNG", amount: "850.00" }] }] });
  fireEvent.click(await screen.findByRole("button", { name: "Aufteilen" }));
  await screen.findByRole("option", { name: /Anna Muster.*2025-01-01/ });
  const select = screen.getByRole("combobox", { name: /^Zielart$/ });
  expect(select).toHaveAccessibleName("Zielart");
  expect(select).toHaveAttribute("aria-label", "Zielart");
});
