import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api/client";
import { DunningNoticesPage } from "./DunningNoticesPage";
import { DunningNoticeDetailPage } from "./DunningNoticeDetailPage";

const auth = vi.hoisted(() => ({ role: "CLERK", organization_id: "org" }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: auth }) }));
vi.mock("../components/AccountingScopeSelector", () => ({ AccountingScopeSelector: (p: { onChange: (id: string) => void; onValidityChange: (valid: boolean) => void }) => <button type="button" onClick={() => { p.onChange("scope"); p.onValidityChange(true); }}>Kreis wählen</button> }));
const notice = { id: "notice", status: "issued", level: 1, issue_date: "2026-03-02", due_date: "2026-03-16", principal_amount: "700.00", fee_amount: "0.00", interest_amount: "8.63", total_amount: "708.63", included_items: [], interest_snapshot: { base_interest_rate: "2.50", annual_rate: "7.50", reference_date: "2026-03-02", items: [{ payment_type: "KALTMIETE", period_year: 2026, period_month: 1, due_date: "2026-01-01", days: 60, principal: "700.00", interest: "8.63" }] } };
const defaults = { fee_level_1: "0.00", fee_level_2: "2.50", fee_level_3: "2.50", default_interest_enabled: false };
let settings: () => Promise<typeof defaults>;
beforeEach(() => {
  auth.role = "CLERK"; auth.organization_id = "org";
  settings = async () => defaults;
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url === "/api/dunning/settings") return settings();
    if (url === "/api/dunning/notice") return notice;
    if (url.startsWith("/api/dunning")) return [];
    if (url === "/api/tenant-profiles") return [{ id: "tenant", person_id: "person" }];
    if (url === "/api/owner-profiles") return [{ id: "owner", person_id: "person" }];
    if (url === "/api/persons") return [{ id: "person", first_name: "Ada", last_name: "Test" }];
    if (url.startsWith("/api/leases")) return [{ id: "lease", tenant_profile_id: "tenant", unit_id: "unit", valid_from: "2026-01-01" }];
    if (url.startsWith("/api/units")) return [{ id: "unit", property_id: "property" }];
    if (url.startsWith("/api/properties")) return [{ id: "property", name: "Haus", management_type: "WEG" }];
    if (url.startsWith("/api/economic-plans")) return [];
    throw new Error(`Unexpected GET ${url}`);
  });
  vi.spyOn(api, "post").mockResolvedValue(notice);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function show() { return render(<MemoryRouter initialEntries={["/mahnungen?create=tenant&tenant_profile_id=tenant&lease_agreement_id=lease"]}><Routes><Route path="/mahnungen" element={<DunningNoticesPage />} /><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>); }
async function ready() { await screen.findByRole("option", { name: /Ada Test ab/ }); fireEvent.click(screen.getByRole("button", { name: "Kreis wählen" })); }
it("reads organization settings without offering a clerk an edit", async () => {
  show(); await ready();
  expect(await screen.findByLabelText("Mahngebühr Stufe 2 (EUR)")).toHaveValue("2,50");
  expect(screen.getByLabelText("Mahngebühr Stufe 2 (EUR)")).toHaveAttribute("readonly");
  expect(screen.getByLabelText("Verzugszinsen automatisch berechnen")).toBeDisabled();
  expect(screen.queryByText("Mahneinstellungen speichern")).toBeNull();
});

it("an administrator saves the three fees as exact decimal text and enables interest", async () => {
  auth.role = "OWNER_ADMIN";
  const updated = { ...defaults, fee_level_2: "3.00", fee_level_3: "4.00", default_interest_enabled: true };
  vi.spyOn(api, "put").mockResolvedValue(updated);
  show(); await ready();
  const fee = await screen.findByLabelText("Mahngebühr Stufe 2 (EUR)");
  fireEvent.change(fee, { target: { value: "3,00" } });
  fireEvent.change(screen.getByLabelText("Mahngebühr Stufe 3 (EUR)"), { target: { value: "4,00" } });
  fireEvent.click(screen.getByLabelText("Verzugszinsen automatisch berechnen"));
  fireEvent.submit(fee.closest("form")!);
  expect(api.put).toHaveBeenCalledWith("/api/dunning/settings", updated);
  await screen.findByText("Mahneinstellungen gespeichert.");
});

it("creates the tenant notice without a manual rate", async () => {
  show(); await ready();
  expect(screen.queryByLabelText("Basiszinssatz (%)")).toBeNull();
  fireEvent.submit(screen.getByRole("button", { name: "Mahnung erstellen" }).closest("form")!);
  await screen.findByText("Verzugszinsen");
  expect(api.post).toHaveBeenCalledWith("/api/dunning/tenant", expect.objectContaining({ accounting_scope_id: "scope" }));
  expect(vi.mocked(api.post).mock.calls[0][1]).not.toHaveProperty("base_interest_rate");
});

it("shows frozen interest, rates, reference date and item days without recalculation", async () => {
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("Verzugszinsen")).toBeVisible();
  expect(screen.getAllByText("8,63 €").length).toBeGreaterThan(0);
  expect(screen.getByText("708,63 €")).toBeVisible();
  expect(screen.getByText(/2.50 %/)).toBeVisible(); expect(screen.getByText(/7.50 %/)).toBeVisible();
  expect(screen.getAllByText("2026-03-02").length).toBeGreaterThan(0);
  const row = screen.getByRole("row", { name: /Kaltmiete/ });
  expect(within(row).getByText("60")).toBeVisible(); expect(within(row).getByText("01.01.2026")).toBeVisible();
});

it("puts complete principal, interest and open amounts before descriptive columns", async () => {
  vi.mocked(api.get).mockImplementation(async url => url === "/api/dunning/notice" ? {
    ...notice,
    included_items: Array.from({ length: 12 }, (_, month) => ({ payment_type: "NK_VORAUSZAHLUNG", period_year: 2026, period_month: month + 1, soll: "1234.56", ist: "0.00", open_amount: "1234.56" })),
    interest_snapshot: { ...notice.interest_snapshot, items: Array.from({ length: 12 }, (_, month) => ({ ...notice.interest_snapshot.items[0], period_month: month + 1, principal: "1234.56", interest: "15.22" })) },
  } : []);
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByText("Eingefrorene Zinsberechnung");
  const tables = screen.getAllByRole("table").filter(table => within(table).queryAllByRole("columnheader").length);
  expect(within(tables[0]).getAllByRole("columnheader").map(cell => cell.textContent)).toEqual(["Hauptforderung", "Zinsen", "Typ", "Periode", "Fälligkeit", "Tage"]);
  expect(within(tables[1]).getAllByRole("columnheader").map(cell => cell.textContent)).toEqual(["Offen", "Soll", "Ist", "Periode", "Typ"]);
  expect(within(tables[0]).getAllByRole("cell").slice(0, 2).map(cell => cell.textContent)).toEqual(["1.234,56 €", "15,22 €"]);
  expect(within(tables[1]).getAllByRole("cell").slice(0, 3).map(cell => cell.textContent)).toEqual(["1.234,56 €", "1.234,56 €", "0,00 €"]);
  for (const table of tables) expect(within(table).getAllByRole("row")).toHaveLength(13);
});

it.each(["KALTMIETE", "NK_VORAUSZAHLUNG"])("shows the stored interest start only on the new %s row alongside legacy data", async payment_type => {
  const legacy = notice.interest_snapshot.items[0];
  vi.mocked(api.get).mockImplementation(async url => url === "/api/dunning/notice" ? {
    ...notice, interest_snapshot: { ...notice.interest_snapshot, items: [legacy,
      { ...legacy, payment_type, interest_start: "2026-04-08" }] },
  } : []);
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText(/Zinsbeginn:/)).toHaveTextContent("08.04.2026");
  expect(screen.getAllByText(/Zinsbeginn:/)).toHaveLength(1);
  expect(screen.queryByText(/02.01.2026/)).toBeNull();
});

it.each([
  ["2026-11-01T12:26:04.611054+00:00", "01.11.2026, 13:26 Uhr"],
  ["2026-07-01T12:26:04+00:00", "01.07.2026, 14:26 Uhr"],
])("formats the issued instant %s consistently in German time", async (issued_at, expected) => {
  vi.mocked(api.get).mockImplementation(async url => url === "/api/dunning/notice" ? { ...notice, issued_at } : []);
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText(`Ausgestellt am: ${expected}`)).toBeVisible();
  expect(screen.getByRole("row", { name: `Ausgestellt am ${expected}` })).toBeVisible();
  expect(screen.queryByText(issued_at)).toBeNull();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
it("posts owner scope without a plan or manual interest rate", async () => {
  show(); await ready();
  fireEvent.change(screen.getByLabelText("Empfaengertyp *"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("Eigentuemer *"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("WEG-Objekt *"), { target: { value: "property" } });
  fireEvent.click(screen.getByRole("button", { name: "Kreis wählen" }));
  const button = screen.getByRole("button", { name: "Mahnung erstellen" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.submit(button.closest("form")!);
  await screen.findByText("Verzugszinsen");
  expect(api.post).toHaveBeenCalledWith("/api/dunning/owner", expect.objectContaining({ owner_profile_id: "owner", economic_plan_id: null, accounting_scope_id: "scope" }));
  expect(vi.mocked(api.post).mock.calls[0][1]).not.toHaveProperty("base_interest_rate");
});

it("preserves input on POST error and synchronously locks duplicate submissions", async () => {
  const post = deferred<unknown>(); vi.mocked(api.post).mockReturnValue(post.promise);
  show(); await ready();
  const input = screen.getByLabelText("Mahngebuehr (Override)"); fireEvent.change(input, { target: { value: "4" } });
  act(() => { fireEvent.submit(input.closest("form")!); fireEvent.submit(input.closest("form")!); });
  expect(api.post).toHaveBeenCalledTimes(1); expect(input).toBeDisabled();
  await act(async () => post.reject(new Error("POST offline")));
  expect(await screen.findByText(/POST offline/)).toBeVisible(); expect(input).toHaveValue("4"); expect(input).toBeEnabled();
});
it.each(["READ_ONLY", "UNKNOWN"])("%s cannot create even with a creation URL", async role => {
  auth.role = role; show(); await screen.findByText("Keine Mahnungen vorhanden.");
  expect(screen.queryByLabelText("Basiszinssatz (%)")).toBeNull();
  expect(screen.queryByRole("button", { name: "+ Neue Mahnung" })).toBeNull(); expect(api.post).not.toHaveBeenCalled();
});
it("keeps legacy zero-interest letters and the issued-only PDF action unchanged", async () => {
  vi.mocked(api.get).mockImplementation(async url => url === "/api/dunning/notice" ? { ...notice, interest_amount: "0.00", interest_snapshot: null, total_amount: "700.00" } : []);
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByText("Verzugszinsen"); expect(screen.getAllByText("0,00 €")).toHaveLength(2);
  expect(screen.queryByText("Eingefrorene Zinsberechnung")).toBeNull();
  expect(screen.getByRole("button", { name: "PDF herunterladen" })).toBeVisible();
});

it("ignores stale settings from another organization", async () => {
  const old = deferred<typeof defaults>(); settings = () => old.promise;
  const page = show(); await ready();
  const next = deferred<typeof defaults>(); settings = () => next.promise;
  auth.organization_id = "other-org";
  page.rerender(<MemoryRouter><Routes><Route path="/mahnungen" element={<DunningNoticesPage />} /></Routes></MemoryRouter>);
  await act(async () => old.resolve({ ...defaults, fee_level_2: "99.00" }));
  expect(screen.queryByLabelText("Mahngebühr Stufe 2 (EUR)")).toBeNull();
  await act(async () => next.resolve({ ...defaults, fee_level_2: "3.00" }));
  expect(screen.getByLabelText("Mahngebühr Stufe 2 (EUR)")).toHaveValue("3,00");
});
it("does not fetch current settings or offer PDF for a frozen draft", async () => {
  vi.mocked(api.get).mockImplementation(async url => url === "/api/dunning/notice" ? { ...notice, status: "draft", interest_amount: "8.6300", total_amount: "708.6300" } : []);
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  await screen.findAllByText("8,63 €"); expect(screen.getByText("708,63 €")).toBeVisible();
  expect(screen.queryByRole("button", { name: "PDF herunterladen" })).toBeNull();
  expect(api.get).not.toHaveBeenCalledWith("/api/dunning/settings");
});
