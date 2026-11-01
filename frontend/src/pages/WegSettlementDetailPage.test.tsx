import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import * as endpoints from "../api/endpoints";
import type { EconomicPlan, OwnerProfile, Person, Property, WegLine, WegRun } from "../api/types";
import { WegSettlementDetailPage } from "./WegSettlementDetailPage";

const seller = "00000000-0000-0000-0000-000000000001";
const buyer = "00000000-0000-0000-0000-000000000002";
const outsider = "00000000-0000-0000-0000-000000000003";
const runId = "10000000-0000-0000-0000-000000000001";
const nextRunId = "10000000-0000-0000-0000-000000000002";
const property: Property = { id: "property-weg", name: "WEG Haus", street: "Weg 1", zip_code: "10115", city: "Berlin", management_type: "WEG", fiscal_year_start_month: 1, mea_denominator: 1000, mea_scale_status: "CONFIRMED" };
const profiles: OwnerProfile[] = [seller, buyer, outsider].map(id => ({ id, person_id: `person-${id}`, notes: "" }));
const people: Person[] = [[seller, "Anna", "Alt"], [buyer, "Berta", "Neu"], [outsider, "Clara", "Fremd"]].map(([id, first_name, last_name]) => ({
  id: `person-${id}`, first_name, last_name, can_prepare: false, can_export: false,
  company_name: null, email: null, phone: null, street: "", zip_code: "", city: "", iban: null, bic: null, bank_name: null,
}));
const plan: EconomicPlan = {
  id: "plan-seller-only", organization_id: "org", property_id: property.id, management_mandate_id: null,
  plan_year: 2026, period_start: "2026-01-01", period_end: "2026-12-31", monthly_due_day: 1,
  reserve_contribution_total: "0.00", status: "finalized", snapshot_sha256: "a".repeat(64), engine_version: "1",
  finalized_at: "2026-01-01T10:00:00Z", finalized_by: "admin", superseded_by_id: null,
  lines: [{ id: "plan-line-seller", line_type: "ADVANCE", cost_category_code: null, owner_profile_id: seller, unit_id: "unit",
    label: "Nur Verkäufer im Nullplan", distribution_key: "MEA", distribution_base_total: "1000", distribution_base_share: "1000",
    planned_amount: "0.00", monthly_amount: "0.00", cost_group: null, sort_order: 1, note: null }],
};
function draft(id = runId): WegRun {
  return { id, organization_id: "org", property_id: property.id, run_type: "WEG_JAHRESABRECHNUNG", period_start: "2026-01-01", period_end: "2026-12-31",
    economic_plan_id: plan.id, total_cost: null, balance: null, status: "draft", snapshot_sha256: null,
    engine_version: "1", finalized_at: null, superseded_by_id: null, lines: [] };
}
function frozenLine(id: string, owner: string, amount: string): WegLine {
  return { id, line_type: "COST", addressee_owner_profile_id: owner, cost_category_code: "WASSER", label: `Festgeschriebener Anteil ${id}`,
    distribution_key: "MEA", total_amount: "365.00", distribution_base_total: "1000", distribution_base_share: "1000",
    time_share_days: owner === seller ? 181 : 184, time_share_total_days: 365, amount, sort_order: 1, note: "Unveränderter Snapshot" };
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
function install(runs: WegRun[] = [draft()], ownersReply: (id: string) => Response | Promise<Response> = () => json([seller, buyer])) {
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/reserve-ledger')) return json({ opening_balance: '0.00', contributions_total: '0.00', withdrawals_total: '0.00', overrides_total: '0.00', closing_balance: '0.00', planned_contributions_total: '0.00', entries: [], frozen: false, overdrawn: false, booking_basis: 'CASH' });
    if (url === "/api/properties") return json([property]);
    if (url === "/api/owner-profiles") return json(profiles);
    if (url === "/api/persons") return json(people);
    if (url === `/api/economic-plans/${plan.id}`) return json(plan);
    if (url.startsWith("/api/financial-input-coverage/source-options?")) return json({ items: [], next_cursor: null });
    if (url === "/api/financial-input-coverage" && init?.method === "POST") return json({ id: "coverage" }, 201);
    for (const run of runs) {
      if (url === `/api/weg-settlements/${run.id}`) return json(run);
      if (url === `/api/weg-settlements/${run.id}/input-owners`) return ownersReply(run.id);
      if (url === `/api/weg-settlements/${run.id}/compute` && init?.method === "POST") return json({ ...run, total_cost: "365.00" });
    }
    throw new Error(`Unexpected request ${init?.method ?? "GET"} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
function show() {
  return render(<MemoryRouter initialEntries={[`/weg-abrechnungen/${runId}`]}><Link to={`/weg-abrechnungen/${nextRunId}`}>Nächster Vorgang</Link><Routes>
    <Route path="/weg-abrechnungen/:runId" element={<WegSettlementDetailPage />} />
  </Routes></MemoryRouter>);
}

it('shows the current opening beside the corrected balance input and reason', async () => {
  install(); show();
  expect(await screen.findByText(/Aktueller Anfangsbestand: 0,00/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Rücklagen-Anfangsbestand'), { target: { value: '5500.00' } });
  expect(screen.getByLabelText('Rücklagen-Anfangsbestand')).toHaveValue('5500.00');
  expect(screen.getByLabelText('Begründung der Rücklagenwerte')).toBeInTheDocument();
});
const paymentLabel = (name: string) => `Tatsächlich gezahltes Hausgeld · ${name}`;
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("reads the subject-bound input-owner IDs through the typed endpoint", async () => {
  const fetchMock = install();
  expect(endpoints.getWegInputOwners).toBeTypeOf("function");
  expect(await endpoints.getWegInputOwners(runId)).toEqual([seller, buyer]);
  expect(fetchMock).toHaveBeenCalledWith(`/api/weg-settlements/${runId}/input-owners`, expect.objectContaining({ credentials: "include" }));
});

it("uses both coverage owners despite seller-only plan lines and sends two explicit zero payments", async () => {
  const fetchMock = install(); show();
  const sellerInput = await screen.findByRole("textbox", { name: paymentLabel("Anna Alt") });
  const buyerInput = await screen.findByRole("textbox", { name: paymentLabel("Berta Neu") });
  expect(screen.getByLabelText('Begründung der Rücklagenwerte').closest('.form-grid')).not.toBeNull();
  expect(plan.lines.map(line => line.owner_profile_id)).toEqual([seller]);
  expect(sellerInput).toHaveValue(""); expect(buyerInput).toHaveValue("");
  expect(screen.queryByRole("textbox", { name: paymentLabel("Clara Fremd") })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  fireEvent.change(sellerInput, { target: { value: "0" } });
  expect(buyerInput).toHaveValue("");
  fireEvent.change(buyerInput, { target: { value: "0" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Rücklagen-Anfangsbestand" }), { target: { value: "0" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Rücklagen-Entnahmen (Jahr)" }), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText('Begründung der Rücklagenwerte'), { target: { value: 'Geprüfter Import' } });
  fireEvent.change(screen.getByRole("textbox", { name: "Grundlage der Prüfung" }), { target: { value: "Beide Eigentümer und Kontoauszüge vollständig geprüft" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Alle angezeigten Beträge und Listen sind vollständig geprüft, einschließlich bestätigter Nullbeträge." }));
  fireEvent.click(screen.getByRole("button", { name: "Datengrundlage speichern" }));
  await screen.findByRole("status");
  const coverage = fetchMock.mock.calls.find(([url]) => url === "/api/financial-input-coverage")!;
  const values = { reserve_opening_balance: "0", reserve_withdrawals_total: "0", reserve_override_reason: 'Geprüfter Import', paid_advances: [
    { owner_profile_id: seller, paid_total: "0" }, { owner_profile_id: buyer, paid_total: "0" },
  ] };
  expect(JSON.parse(coverage[1]!.body as string)).toMatchObject({ subject_kind: "settlement", subject_id: runId, values });
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/compute"))).toBe(true));
  const compute = fetchMock.mock.calls.find(([url]) => url.endsWith("/compute"))!;
  expect(JSON.parse(compute[1]!.body as string)).toEqual(values);
});

it.each([403, 404, "network"] as const)("fails closed with a visible input-owner error %s, without coverage or zero fallbacks", async failure => {
  const fetchMock = install([draft()], () => {
    if (failure === "network") throw new TypeError("Owner-Liste nicht erreichbar");
    return json({ detail: "Owner-Liste nicht zugänglich" }, failure);
  }); show();
  expect(await screen.findByRole("alert")).toHaveTextContent(failure === "network" ? "Owner-Liste nicht erreichbar" : "Owner-Liste nicht zugänglich");
  expect(screen.queryByRole("textbox", { name: paymentLabel("Anna Alt") })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Berechnen" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Datengrundlage speichern" })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
});

it("renders finalized frozen lines without requesting current input owners or the old plan", async () => {
  const finalRun: WegRun = { ...draft(), status: "finalized", snapshot_sha256: "b".repeat(64), finalized_at: "2027-02-01T10:00:00Z",
    total_cost: "365.00", balance: "365.00", lines: [frozenLine("seller", seller, "181.00"), frozenLine("buyer", buyer, "184.00")] };
  const original = JSON.stringify(finalRun);
  const fetchMock = install([finalRun], () => { throw new Error("Finalized runs must not read live inputs"); }); show();
  expect(await screen.findByRole("heading", { name: "Anna Alt" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Berta Neu" })).toBeVisible();
  expect(screen.getByText("Festgeschriebener Anteil seller")).toBeVisible();
  expect(screen.getByText("181.00 EUR")).toBeVisible(); expect(screen.getByText("184.00 EUR")).toBeVisible();
  expect(screen.getByText("b".repeat(64))).toBeVisible();
  expect(screen.queryByRole("textbox", { name: paymentLabel("Anna Alt") })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith("/input-owners") || url.startsWith("/api/economic-plans/"))).toBe(false);
  expect(JSON.stringify(finalRun)).toBe(original);
});

it("ignores late owner IDs from a previous run after route navigation", async () => {
  let finish!: (response: Response) => void;
  const fetchMock = install([draft(), draft(nextRunId)], id => id === runId
    ? new Promise<Response>(resolve => { finish = resolve; }) : json([buyer]));
  show();
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === `/api/weg-settlements/${runId}/input-owners`)).toBe(true));
  fireEvent.click(screen.getByRole("link", { name: "Nächster Vorgang" }));
  expect(await screen.findByRole("textbox", { name: paymentLabel("Berta Neu") })).toHaveValue("");
  await act(async () => { finish(json([seller, outsider])); });
  expect(screen.queryByRole("textbox", { name: paymentLabel("Anna Alt") })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: paymentLabel("Clara Fremd") })).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: paymentLabel("Berta Neu") })).toHaveValue("");
});

it.each([
  ["object", { id: runId, lines: [] }],
  ["null", null],
  ["string", seller],
  ["non-string owner", [seller, 42]],
])("rejects malformed input-owner roster %s without financial fallbacks and retries explicitly", async (_label, body) => {
  let malformed = true;
  const fetchMock = install([draft()], () => json(malformed ? body : [seller, buyer]));
  show();
  expect(await screen.findByRole("alert")).toHaveTextContent("Eigentümerliste hat ein ungültiges Format. Bitte erneut laden.");
  expect(screen.queryByRole("button", { name: "Berechnen" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Datengrundlage speichern" })).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: paymentLabel("Anna Alt") })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
  malformed = false;
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));
  expect(await screen.findByRole("textbox", { name: paymentLabel("Anna Alt") })).toHaveValue("");
  expect(screen.getByRole("textbox", { name: paymentLabel("Berta Neu") })).toHaveValue("");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([url]) => url.endsWith("/input-owners"))).toHaveLength(2);
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);
});
