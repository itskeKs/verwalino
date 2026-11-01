import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError } from "../api/client";
import * as endpoints from "../api/endpoints";
import { SettlementsPage } from "./SettlementsPage";
import type { SettlementRun } from "../api/types";

const auth = vi.hoisted(() => ({ role: "CLERK" }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: auth.role } }) }));
const created = [1, 2, 3].map(n => ({ id: `run-${n}`, property_id: "property-1", lease_agreement_id: `lease-${n}`, period_start: "2025-07-01", period_end: "2026-06-30", run_type: "BK", status: "draft", advance_payments_total: null, balance: null, lines: [] }) as unknown as SettlementRun);
let rows: SettlementRun[];
beforeEach(() => {
  auth.role = "CLERK"; rows = [];
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url.includes("/settlement-periods")) return [];
    if (url.startsWith("/api/settlements")) return rows;
    if (url === "/api/properties") return [{ id: "property-1", name: "Sonnenhof" }, { id: "property-2", name: "Gartenhof" }];
    if (url === "/api/leases") return [1, 2, 3].map(n => ({ id: `lease-${n}`, unit_id: `unit-${n}`, tenant_profile_id: "tenant-1", valid_from: "2025-01-01", valid_to: null }));
    if (url === "/api/units") return [1, 2, 3].map(n => ({ id: `unit-${n}`, number: `Wohnung ${n}` }));
    if (url === "/api/tenant-profiles") return [{ id: "tenant-1", person_id: "person-1" }];
    if (url === "/api/persons") return [{ id: "person-1", first_name: "Ada", last_name: "Mieter" }];
    if (url.startsWith("/api/accounting-scopes/page?")) return { items: [{ id: "scope-1", property_id: "property-1", label: "Mietkreis", scope_type: "MIET", valid_from: "2025-01-01", valid_to: null }], next_cursor: null };
    throw new Error(`Unexpected GET: ${url}`);
  });
  vi.spyOn(api, "post").mockImplementation(async url => {
    if (url !== "/api/settlements/batch") throw new Error(`Unexpected POST: ${url}`);
    rows = created; return { created, skipped: [] };
  });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function show(path = "/abrechnungen") { return render(<MemoryRouter initialEntries={[path]}><SettlementsPage /></MemoryRouter>); }
async function open(path?: string) {
  const view = show(path);
  await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.click(screen.getByRole("button", { name: "BK-Sammellauf" }));
  const region = screen.getByRole("region", { name: "BK-Sammellauf" });
  fireEvent.change(within(region).getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-1" } });
  fireEvent.change(within(region).getByLabelText("Zeitraum von *"), { target: { value: "2025-07-01" } });
  fireEvent.change(within(region).getByLabelText("Zeitraum bis *"), { target: { value: "2026-06-30" } });
  return { ...view, region, submit: within(region).getByRole("button", { name: "Entwürfe anlegen" }) };
}
it("exposes the typed batch helper and forwards only the batch payload", async () => {
  expect(endpoints).toHaveProperty("createSettlementBatch");
  const body = { property_id: "property-1", period_start: "2025-07-01", period_end: "2026-06-30", accounting_scope_id: "scope-1" };
  await endpoints.createSettlementBatch(body);
  expect(api.post).toHaveBeenCalledWith("/api/settlements/batch", body);
});
it("names the existing list filters accessibly", async () => {
  show(); await screen.findByRole("option", { name: "Sonnenhof" });
  expect(screen.getByRole("combobox", { name: "Statusfilter" })).toBeVisible();
  expect(screen.getByRole("combobox", { name: "Objektfilter" })).toBeVisible();
});
it("creates three drafts, reloads human labels, preserves unknown money and permits same-period repeat", async () => {
  const { submit, region } = await open();
  fireEvent.click(submit);
  expect(await within(region).findByRole("status")).toHaveTextContent("3 Entwürfe angelegt · 0 bereits vorhanden");
  await screen.findByRole("table");
  expect(screen.getByText(/Wohnung 1 — Ada Mieter/)).toBeVisible();
  expect(screen.queryByText("0.00 EUR")).toBeNull();
  expect(api.get).toHaveBeenCalledTimes(14);
  expect(api.post).toHaveBeenCalledWith("/api/settlements/batch", { property_id: "property-1", period_start: "2025-07-01", period_end: "2026-06-30", accounting_scope_id: null });
  vi.mocked(api.post).mockResolvedValueOnce({ created: [], skipped: created.map(run => ({ lease_agreement_id: run.lease_agreement_id, settlement_run_id: run.id })) });
  fireEvent.click(submit);
  await waitFor(() => expect(within(region).getByRole("status")).toHaveTextContent("0 Entwürfe angelegt · 3 bereits vorhanden"));
  expect(api.post).toHaveBeenCalledTimes(2);
});

it.each(["OWNER_ADMIN", "CLERK"])("offers batch for %s", async role => {
  auth.role = role; show();
  expect(await screen.findByRole("button", { name: "BK-Sammellauf" })).toBeVisible();
});
it.each(["READ_ONLY", "UNKNOWN", ""])("does not offer batch for role %s", async role => {
  auth.role = role; show(); await screen.findByRole("option", { name: "Sonnenhof" });
  expect(screen.queryByRole("button", { name: "BK-Sammellauf" })).toBeNull();
});
it.each(["", "2025-06-30"])("does not post missing or reversed end date %s", async value => {
  const { region, submit } = await open();
  fireEvent.change(within(region).getByLabelText("Zeitraum bis *"), { target: { value } });
  fireEvent.click(submit);
  expect(within(region).getByRole("alert")).toBeVisible();
  expect(api.post).not.toHaveBeenCalled();
});
it("does not post missing property or start date", async () => {
  const { region, submit } = await open();
  fireEvent.change(within(region).getByRole("combobox", { name: "Objekt *" }), { target: { value: "" } });
  fireEvent.click(submit); expect(api.post).not.toHaveBeenCalled();
  fireEvent.change(within(region).getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-1" } });
  fireEvent.change(within(region).getByLabelText("Zeitraum von *"), { target: { value: "" } });
  fireEvent.click(submit); expect(api.post).not.toHaveBeenCalled();
});
it("posts explicit property-bound scope without advance or mandate overrides and resets scope/results on property change", async () => {
  const { region, submit } = await open();
  await within(region).findByRole("option", { name: /Mietkreis/ });
  fireEvent.change(within(region).getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope-1" } });
  fireEvent.click(submit);
  await within(region).findByRole("status");
  expect(api.post).toHaveBeenLastCalledWith("/api/settlements/batch", { property_id: "property-1", period_start: "2025-07-01", period_end: "2026-06-30", accounting_scope_id: "scope-1" });
  expect(api.get).toHaveBeenCalledWith("/api/accounting-scopes/page?capability=prepare&financial_creation=true&property_id=property-1&scope_type=MIET&q=", expect.any(Object));
  fireEvent.change(within(region).getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-2" } });
  expect(within(region).queryByRole("status")).toBeNull();
  expect(within(region).getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("");
  fireEvent.change(within(region).getByLabelText("Zeitraum von *"), { target: { value: "2025-07-01" } });
  fireEvent.change(within(region).getByLabelText("Zeitraum bis *"), { target: { value: "2026-06-30" } });
  fireEvent.click(submit);
  await within(region).findByRole("status");
  expect(api.post).toHaveBeenLastCalledWith("/api/settlements/batch", { property_id: "property-2", period_start: "2025-07-01", period_end: "2026-06-30", accounting_scope_id: null });
});
it.each(["property-1", "foreign-property"])("prefills only a legitimate property filter %s", async id => {
  show(`/abrechnungen?property_id=${id}`);
  await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.click(screen.getByRole("button", { name: "BK-Sammellauf" }));
  expect(within(screen.getByRole("region", { name: "BK-Sammellauf" })).getByRole("combobox", { name: "Objekt *" })).toHaveValue(id === "property-1" ? id : "");
});
it("locks same-tick double submits and disables inputs, scope and abort while pending", async () => {
  const { region, submit } = await open();
  let finish!: (value: unknown) => void;
  vi.mocked(api.post).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const form = submit.closest("form")!;
  act(() => { fireEvent.submit(form); fireEvent.submit(form); });
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(submit).toBeDisabled();
  expect(within(region).getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  expect(within(region).getByRole("combobox", { name: "Objekt *" })).toBeDisabled();
  expect(within(region).getByLabelText("Zeitraum von *")).toBeDisabled();
  expect(within(region).getByLabelText("Zeitraum bis *")).toBeDisabled();
  expect(within(region).getByRole("combobox", { name: "Abrechnungskreis" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "+ Neue Abrechnung" })).toBeDisabled();
  await act(async () => finish({ created, skipped: [] }));
  expect(submit).not.toBeDisabled();
});
it.each([403, 404, 409, 422, "network"])("preserves values and retries after %s", async status => {
  const { region, submit } = await open();
  vi.mocked(api.post).mockRejectedValueOnce(typeof status === "number" ? new ApiError(status, `Fehler ${status}`) : new TypeError("Netzwerk nicht verfügbar"));
  fireEvent.click(submit);
  expect(await within(region).findByRole("alert")).toHaveTextContent(typeof status === "number" ? `Fehler ${status}` : "Netzwerk nicht verfügbar");
  expect(within(region).getByRole("combobox", { name: "Objekt *" })).toHaveValue("property-1");
  expect(within(region).getByLabelText("Zeitraum von *")).toHaveValue("2025-07-01");
  expect(within(region).getByLabelText("Zeitraum bis *")).toHaveValue("2026-06-30");
  fireEvent.click(submit);
  await within(region).findByRole("status");
  expect(within(region).queryByRole("alert")).toBeNull();
  expect(api.post).toHaveBeenCalledTimes(2);
});
it.each([false, true])("ignores a late batch response after unmount (reject=%s)", async reject => {
  const { unmount, submit } = await open();
  let finish!: (value: unknown) => void;
  let fail!: (reason: Error) => void;
  vi.mocked(api.post).mockImplementationOnce(() => new Promise((resolve, rejectPromise) => { finish = resolve; fail = rejectPromise; }));
  fireEvent.click(submit);
  const gets = vi.mocked(api.get).mock.calls.length;
  unmount();
  await act(async () => { if (reject) fail(new Error("Late error")); else finish({ created, skipped: [] }); });
  expect(api.get).toHaveBeenCalledTimes(gets);
});
it("invalidates a pending response when form generation changes", async () => {
  const { region, submit } = await open();
  let finish!: (value: unknown) => void;
  vi.mocked(api.post).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  fireEvent.click(submit);
  const gets = vi.mocked(api.get).mock.calls.length;
  // Synthetic change exercises the generation guard even though real user inputs are disabled.
  fireEvent.change(within(region).getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-2" } });
  await act(async () => finish({ created, skipped: [] }));
  expect(within(region).queryByRole("status")).toBeNull();
  expect(api.get).toHaveBeenCalledTimes(gets + 2);
});
it("never displays single and batch forms together and leaves single creation intact", async () => {
  const { region } = await open();
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Abrechnung" }));
  expect(region).not.toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Neue Abrechnung anlegen" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "BK-Sammellauf" }));
  expect(screen.queryByRole("heading", { name: "Neue Abrechnung anlegen" })).toBeNull();
  fireEvent.click(within(screen.getByRole("region", { name: "BK-Sammellauf" })).getByRole("button", { name: "Abbrechen" }));
  expect(screen.queryByRole("region", { name: "BK-Sammellauf" })).toBeNull();
});

it.each([["2099-04-15", "2099-04-15"], ["2024-02-29", "2024-03-05"]])("accepts arbitrary valid period %s to %s without a calendar-year rule", async (start, end) => {
  const { region, submit } = await open();
  fireEvent.change(within(region).getByLabelText("Zeitraum von *"), { target: { value: start } });
  fireEvent.change(within(region).getByLabelText("Zeitraum bis *"), { target: { value: end } });
  fireEvent.click(submit); await within(region).findByRole("status");
  expect(api.post).toHaveBeenLastCalledWith("/api/settlements/batch", { property_id: "property-1", period_start: start, period_end: end, accounting_scope_id: null });
});
it("retains the existing single-create payload and detail navigation", async () => {
  render(<MemoryRouter initialEntries={["/abrechnungen"]}><Routes>
    <Route path="/abrechnungen" element={<SettlementsPage />} />
    <Route path="/abrechnungen/:id" element={<p>Einzelabrechnung geöffnet</p>} />
  </Routes></MemoryRouter>);
  await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Abrechnung" }));
  const heading = screen.getByRole("heading", { name: "Neue Abrechnung anlegen" });
  const form = heading.parentElement!.querySelector("form")!;
  fireEvent.change(within(form).getByLabelText(/^Objekt/), { target: { value: "property-1" } });
  fireEvent.change(within(form).getByLabelText(/^Mietvertrag/), { target: { value: "lease-1" } });
  fireEvent.change(within(form).getByLabelText("Abrechnungszeitraum von *"), { target: { value: "2025-07-01" } });
  fireEvent.change(within(form).getByLabelText("Abrechnungszeitraum bis *"), { target: { value: "2026-06-30" } });
  vi.mocked(api.post).mockResolvedValueOnce(created[0]);
  fireEvent.click(within(form).getByRole("button", { name: "Anlegen" }));
  await screen.findByText("Einzelabrechnung geöffnet");
  expect(api.post).toHaveBeenCalledExactlyOnceWith("/api/settlements", { property_id: "property-1", lease_agreement_id: "lease-1", period_start: "2025-07-01", period_end: "2026-06-30", advance_payments_total: null, heating_advance_payments_total: "0.00", management_mandate_id: null, accounting_scope_id: null });
});

it.each(["Objekt *", "Mietvertrag *"])("explicitly labels the single-create select %s", async label => {
  show();
  await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Abrechnung" }));
  const heading = screen.getByRole("heading", { name: "Neue Abrechnung anlegen" });
  const form = heading.parentElement!.querySelector("form")!;
  const select = within(form).getByRole("combobox", { name: label });
  expect(select).toHaveAttribute("aria-label", label);
  expect(within(form).getByLabelText(label, { exact: true })).toBe(select);
});

it('normalizes both German advance amounts before creating the statement', async () => {
  show();
  await screen.findByRole('option', { name: 'Sonnenhof' });
  fireEvent.click(screen.getByRole('button', { name: '+ Neue Abrechnung' }));
  const form = screen.getByRole('heading', { name: 'Neue Abrechnung anlegen' }).parentElement!.querySelector('form')!;
  fireEvent.change(within(form).getByLabelText(/^Objekt/), { target: { value: 'property-1' } });
  fireEvent.change(within(form).getByLabelText(/^Mietvertrag/), { target: { value: 'lease-1' } });
  fireEvent.change(within(form).getByLabelText('Abrechnungszeitraum von *'), { target: { value: '2026-01-01' } });
  fireEvent.change(within(form).getByLabelText('Abrechnungszeitraum bis *'), { target: { value: '2026-12-31' } });
  fireEvent.change(within(form).getByLabelText('Betriebskosten-Vorauszahlung (EUR)'), { target: { value: '1800,00' } });
  fireEvent.change(within(form).getByLabelText('Heizkosten-Vorauszahlung (EUR)'), { target: { value: '1080,00' } });
  vi.mocked(api.post).mockResolvedValueOnce(created[0]);
  fireEvent.click(within(form).getByRole('button', { name: 'Anlegen' }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/settlements', expect.objectContaining({
    advance_payments_total: '1800.00', heating_advance_payments_total: '1080.00',
  })));
});

it("offers and prefills the selected lease's clipped span and reclips on a lease change", async () => {
  const original = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(async (url, options) => {
    if (url.includes("/settlement-periods")) return [
      { year: 2025, label: "2025/2026", period_start: "2025-04-01", period_end: "2026-03-31" },
      { year: 2024, label: "2024/2025", period_start: "2024-04-01", period_end: "2025-03-31" },
    ] as never;
    if (url === "/api/leases") return [
      { id: "lease-1", unit_id: "unit-1", tenant_profile_id: "tenant-1", valid_from: "2025-06-15", valid_to: "2025-09-30" },
      { id: "lease-2", unit_id: "unit-1", tenant_profile_id: "tenant-1", valid_from: "2025-10-01", valid_to: null },
    ] as never;
    return original(url, options);
  });
  show(); await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Abrechnung" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-1" } });
  await screen.findByRole("option", { name: "2025/2026" });
  fireEvent.change(screen.getByRole("combobox", { name: "Mietvertrag *" }), { target: { value: "lease-1" } });
  await waitFor(() => expect(screen.getByLabelText("Abrechnungszeitraum von *")).toHaveValue("2025-06-15"));
  expect(screen.getByLabelText("Abrechnungszeitraum bis *")).toHaveValue("2025-09-30");
  expect(screen.getByRole("option", { name: /15.06.2025.*30.09.2025/ })).toBeVisible();
  expect(screen.queryByRole("option", { name: /^2024\/2025/ })).toBeNull();
  fireEvent.change(screen.getByRole("combobox", { name: "Mietvertrag *" }), { target: { value: "lease-2" } });
  await waitFor(() => expect(screen.getByLabelText("Abrechnungszeitraum von *")).toHaveValue("2025-10-01"));
  expect(screen.getByLabelText("Abrechnungszeitraum bis *")).toHaveValue("2026-03-31");
});

it.each(["BK-Sammellauf", "+ Neue Abrechnung"])("offers the property's April period in %s and keeps free dates", async action => {
  const original = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(async (url, options) => url.includes("/settlement-periods") ? [
    { year: 2025, label: "2025/2026", period_start: "2025-04-01", period_end: "2026-03-31" },
  ] : original(url, options));
  show(); await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.click(screen.getByRole("button", { name: action }));
  fireEvent.change(screen.getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-1" } });
  fireEvent.change(await screen.findByRole("combobox", { name: "Abrechnungszeitraum" }), { target: { value: "2025-04-01" } });
  const start = screen.getByLabelText(action === "BK-Sammellauf" ? "Zeitraum von *" : "Abrechnungszeitraum von *");
  const end = screen.getByLabelText(action === "BK-Sammellauf" ? "Zeitraum bis *" : "Abrechnungszeitraum bis *");
  await waitFor(() => expect(start).toHaveValue("2025-04-01"));
  expect(end).toHaveValue("2026-03-31");
  fireEvent.change(end, { target: { value: "2025-12-31" } });
  expect(end).toHaveValue("2025-12-31");
});

it.each([
  ["+ Neue Abrechnung", "Abrechnungszeitraum", "von"],
  ["+ Neue Abrechnung", "Abrechnungszeitraum", "bis"],
  ["BK-Sammellauf", "Zeitraum", "von"],
  ["BK-Sammellauf", "Zeitraum", "bis"],
])("preserves free dates entered before period offers arrive: %s %s %s", async (action, label, field) => {
  let resolveOffers!: (rows: unknown) => void;
  const original = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation((url, options) => url.includes("/settlement-periods")
    ? new Promise(resolve => { resolveOffers = resolve; }) : original(url, options));
  show(); await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.click(screen.getByRole("button", { name: action }));
  fireEvent.change(screen.getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-1" } });
  fireEvent.change(screen.getByLabelText(`${label} ${field} *`), { target: { value: "2025-12-31" } });
  await act(async () => resolveOffers([{ year: 2025, label: "2025/2026", period_start: "2025-04-01", period_end: "2026-03-31" }]));
  expect(screen.getByLabelText(`${label} von *`)).toHaveValue(field === "von" ? "2025-12-31" : "");
  expect(screen.getByLabelText(`${label} bis *`)).toHaveValue(field === "bis" ? "2025-12-31" : "");
});

it.each(["BK-Sammellauf", "+ Neue Abrechnung"])("prefills the backend's last ended November period in %s", async action => {
  const original = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(async (url, options) => url.includes("/settlement-periods") ? [
    { year: 2024, label: "2024/2025", period_start: "2024-11-01", period_end: "2025-10-31" },
    { year: 2023, label: "2023/2024", period_start: "2023-11-01", period_end: "2024-10-31" },
  ] : original(url, options));
  show(); await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.click(screen.getByRole("button", { name: action }));
  fireEvent.change(screen.getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-1" } });
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/properties/property-1/settlement-periods"));
  expect(screen.getByLabelText(action === "BK-Sammellauf" ? "Zeitraum von *" : "Abrechnungszeitraum von *")).toHaveValue("2024-11-01");
  expect(screen.getByLabelText(action === "BK-Sammellauf" ? "Zeitraum bis *" : "Abrechnungszeitraum bis *")).toHaveValue("2025-10-31");
  expect(screen.getAllByRole("option", { name: /^202\d\/202\d$/ })).toHaveLength(2);
});

it("shows a straddling lease error alongside successful batch drafts and existing skips", async () => {
  const { submit, region } = await open();
  vi.mocked(api.post).mockResolvedValueOnce({
    created: [created[0]],
    skipped: [{ lease_agreement_id: "lease-2", settlement_run_id: "run-2" }],
    errors: [{ lease_agreement_id: "lease-3", code: "settlement_period_conflict", error: "Abrechnungszeitraum überschneidet eine finalisierte Abrechnung dieses Mietvertrags." }],
  });
  fireEvent.click(submit);
  expect(await within(region).findByRole("status")).toHaveTextContent("1 Entwürfe angelegt · 1 bereits vorhanden");
  expect(within(region).getByRole("alert")).toHaveTextContent("Wohnung 3 — Ada Mieter");
  expect(within(region).getByRole("alert")).toHaveTextContent("Abrechnungszeitraum überschneidet eine finalisierte Abrechnung dieses Mietvertrags.");
});
