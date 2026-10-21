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
let settings: () => Promise<{ base_interest_rate: string | null }>;
beforeEach(() => {
  auth.role = "CLERK"; auth.organization_id = "org";
  settings = async () => ({ base_interest_rate: "2.50" });
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
it("prefills the configured basis rate without inventing a current official rate", async () => {
  show(); await ready();
  expect(await screen.findByLabelText("Basiszinssatz (%)")).toHaveValue("2.50");
});
it("requires explicit rate input and sends exact negative comma percentages", async () => {
  settings = async () => ({ base_interest_rate: null }); show(); await ready();
  const input = await screen.findByLabelText("Basiszinssatz (%)");
  expect(input).toHaveValue(""); expect(input).toBeRequired();
  expect(screen.getByText(/Kein Basiszinssatz konfiguriert/)).toBeVisible();
  fireEvent.submit(input.closest("form")!); expect(api.post).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "-0,8800" } });
  fireEvent.submit(input.closest("form")!);
  await screen.findByText("Verzugszinsen");
  expect(api.post).toHaveBeenCalledWith("/api/dunning/tenant", expect.objectContaining({ base_interest_rate: "-0.8800", accounting_scope_id: "scope" }));
});
it("shows frozen interest, rates, reference date and item days without recalculation", async () => {
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  expect(await screen.findByText("Verzugszinsen")).toBeVisible();
  expect(screen.getAllByText("8.63 EUR").length).toBeGreaterThan(0);
  expect(screen.getByText("708.63 EUR")).toBeVisible();
  expect(screen.getByText(/2.50 %/)).toBeVisible(); expect(screen.getByText(/7.50 %/)).toBeVisible();
  expect(screen.getAllByText("2026-03-02").length).toBeGreaterThan(0);
  const row = screen.getByRole("row", { name: /Kaltmiete/ });
  expect(within(row).getByText("60")).toBeVisible(); expect(within(row).getByText("2026-01-01")).toBeVisible();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
it.each(["NaN", "Infinity", "-Infinity", "1e2", "2.12345", "2,12345", "2.5%", "1,2.3", ""])('rejects invalid percentage %j without a POST', async value => {
  show(); await ready(); await waitFor(() => expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("2.50"));
  const input = screen.getByLabelText("Basiszinssatz (%)");
  fireEvent.change(input, { target: { value } }); fireEvent.submit(input.closest("form")!);
  expect(api.post).not.toHaveBeenCalled(); expect(screen.getByRole("button", { name: "Mahnung erstellen" })).toBeDisabled();
});
it("retains a manual value when configuration arrives late", async () => {
  const late = deferred<{ base_interest_rate: string | null }>(); settings = () => late.promise;
  show(); await ready(); const input = screen.getByLabelText("Basiszinssatz (%)");
  fireEvent.change(input, { target: { value: "-0,88" } });
  expect(screen.getByRole("button", { name: "Mahnung erstellen" })).toBeDisabled();
  await act(async () => late.resolve({ base_interest_rate: "2.50" }));
  expect(input).toHaveValue("-0,88"); expect(screen.getByRole("button", { name: "Mahnung erstellen" })).toBeEnabled();
});
it("ignores late configuration from a previous recipient context", async () => {
  const old = deferred<{ base_interest_rate: string | null }>(); settings = () => old.promise;
  show(); await ready(); const next = deferred<{ base_interest_rate: string | null }>(); settings = () => next.promise;
  fireEvent.change(screen.getByLabelText("Empfaengertyp *"), { target: { value: "owner" } });
  await act(async () => old.resolve({ base_interest_rate: "9.9999" }));
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("");
  await act(async () => next.resolve({ base_interest_rate: null }));
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("");
});
it("preserves separate manual rates across recipient and source switches and posts owner scope without a plan", async () => {
  show(); await ready(); await waitFor(() => expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("2.50"));
  fireEvent.change(screen.getByLabelText("Basiszinssatz (%)"), { target: { value: "1,2345" } });
  fireEvent.change(screen.getByLabelText("Empfaengertyp *"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("Eigentuemer *"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("WEG-Objekt *"), { target: { value: "property" } });
  fireEvent.change(screen.getByLabelText("Basiszinssatz (%)"), { target: { value: "-0,8800" } });
  fireEvent.click(screen.getByRole("button", { name: "Kreis wählen" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Mahnung erstellen" })).toBeEnabled());
  fireEvent.submit(screen.getByLabelText("Basiszinssatz (%)").closest("form")!);
  await screen.findByText("Verzugszinsen");
  expect(api.post).toHaveBeenCalledWith("/api/dunning/owner", expect.objectContaining({ base_interest_rate: "-0.8800", owner_profile_id: "owner", economic_plan_id: null, accounting_scope_id: "scope" }));
});
it("retries failed settings without overwriting user input", async () => {
  settings = async () => { throw new Error("offline"); }; show(); await ready();
  expect(await screen.findByRole("alert")).toHaveTextContent("offline");
  fireEvent.change(screen.getByLabelText("Basiszinssatz (%)"), { target: { value: "0" } });
  settings = async () => ({ base_interest_rate: "2.50" });
  fireEvent.click(screen.getByRole("button", { name: "Konfiguration erneut laden" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("0");
});
it("preserves input on POST error and synchronously locks duplicate submissions", async () => {
  const post = deferred<unknown>(); vi.mocked(api.post).mockReturnValue(post.promise);
  show(); await ready(); await waitFor(() => expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("2.50"));
  const input = screen.getByLabelText("Basiszinssatz (%)"); fireEvent.change(input, { target: { value: "-0,88" } });
  act(() => { fireEvent.submit(input.closest("form")!); fireEvent.submit(input.closest("form")!); });
  expect(api.post).toHaveBeenCalledTimes(1); expect(input).toBeDisabled();
  await act(async () => post.reject(new Error("POST offline")));
  expect(await screen.findByText(/POST offline/)).toBeVisible(); expect(input).toHaveValue("-0,88"); expect(input).toBeEnabled();
});
it.each(["READ_ONLY", "UNKNOWN"])("%s cannot create even with a creation URL", async role => {
  auth.role = role; show(); await screen.findByText("Keine Mahnungen vorhanden.");
  expect(screen.queryByLabelText("Basiszinssatz (%)")).toBeNull();
  expect(screen.queryByRole("button", { name: "+ Neue Mahnung" })).toBeNull(); expect(api.post).not.toHaveBeenCalled();
});
it("ignores configuration completion after unmount", async () => {
  const late = deferred<{ base_interest_rate: string | null }>(); settings = () => late.promise;
  const page = show(); await ready(); page.unmount();
  await act(async () => late.resolve({ base_interest_rate: "2.50" })); expect(api.post).not.toHaveBeenCalled();
});
it("keeps legacy zero-interest letters and the issued-only PDF action unchanged", async () => {
  vi.mocked(api.get).mockImplementation(async url => url === "/api/dunning/notice" ? { ...notice, interest_amount: "0.00", interest_snapshot: null, total_amount: "700.00" } : []);
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByText("Verzugszinsen"); expect(screen.getAllByText("0.00 EUR")).toHaveLength(2);
  expect(screen.queryByText("Eingefrorene Zinsberechnung")).toBeNull();
  expect(screen.getByRole("button", { name: "PDF herunterladen" })).toBeVisible();
});

it("retains each recipient's manual rate when switching back", async () => {
  show(); await ready(); await waitFor(() => expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("2.50"));
  fireEvent.change(screen.getByLabelText("Basiszinssatz (%)"), { target: { value: "1,2345" } });
  fireEvent.change(screen.getByLabelText("Empfaengertyp *"), { target: { value: "owner" } });
  fireEvent.change(screen.getByLabelText("Basiszinssatz (%)"), { target: { value: "-.88" } });
  fireEvent.change(screen.getByLabelText("Empfaengertyp *"), { target: { value: "tenant" } });
  await waitFor(() => expect(screen.queryByText("Lade Basiszinssatz-Konfiguration…")).toBeNull());
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("1,2345");
  fireEvent.change(screen.getByLabelText("Empfaengertyp *"), { target: { value: "owner" } });
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("-.88");
});
it("ignores stale configuration from another organization", async () => {
  const old = deferred<{ base_interest_rate: string | null }>(); settings = () => old.promise;
  const page = show(); await ready();
  const next = deferred<{ base_interest_rate: string | null }>(); settings = () => next.promise;
  auth.organization_id = "other-org";
  page.rerender(<MemoryRouter initialEntries={["/mahnungen?create=tenant&tenant_profile_id=tenant&lease_agreement_id=lease"]}><Routes><Route path="/mahnungen" element={<DunningNoticesPage />} /></Routes></MemoryRouter>);
  await act(async () => old.resolve({ base_interest_rate: "9.9999" }));
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("");
  await act(async () => next.resolve({ base_interest_rate: "1.2345" }));
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue("1.2345");
});
it.each(["NaN", "Infinity", "2.12345"])("never prefills malformed configured rate %s", async value => {
  settings = async () => ({ base_interest_rate: value }); show(); await ready();
  expect(await screen.findByRole("alert")).toHaveTextContent("Ungültiger konfigurierter Basiszinssatz");
  expect(screen.getByLabelText("Basiszinssatz (%)")).toHaveValue(""); expect(api.post).not.toHaveBeenCalled();
});
it("does not fetch current settings or offer PDF for a frozen draft", async () => {
  vi.mocked(api.get).mockImplementation(async url => url === "/api/dunning/notice" ? { ...notice, status: "draft", interest_amount: "8.6300", total_amount: "708.6300" } : []);
  render(<MemoryRouter initialEntries={["/mahnungen/notice"]}><Routes><Route path="/mahnungen/:dunningId" element={<DunningNoticeDetailPage />} /></Routes></MemoryRouter>);
  await screen.findByText("8.6300 EUR"); expect(screen.getByText("708.6300 EUR")).toBeVisible();
  expect(screen.queryByRole("button", { name: "PDF herunterladen" })).toBeNull();
  expect(api.get).not.toHaveBeenCalledWith("/api/dunning/settings");
});
