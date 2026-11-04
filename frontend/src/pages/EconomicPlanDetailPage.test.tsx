import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { AuthCtx, type AuthContextValue } from "../auth/useAuth";
import { EconomicPlanDetailPage } from "./EconomicPlanDetailPage";
vi.mock("../components/FinancialCoverageReview", () => ({ FinancialCoverageReview: () => null }));
const costs = [{ category_id: "historical-id", category_code: "OLD", label: "Historische Heizung", planned_amount: "7100.10", distribution_key: "MEA", cost_group: "UMLAGEFAEHIG" }];
const draft = { id: "new", property_id: "p", status: "draft", plan_year: 2027, period_start: "2027-01-01", period_end: "2027-12-31", reserve_contribution_total: "6200.20", lines: [], draft_inputs: { planned_costs: costs, reserve_contribution_total: "6200.20" } };
const auth = { loading: false, session: { organization_id: "o", user_id: "u", role: "CLERK" }, login: vi.fn(), logout: vi.fn() } as AuthContextValue;
function show(role = "CLERK") {
  return render(<AuthCtx.Provider value={{ ...auth, session: { ...auth.session!, role } as AuthContextValue["session"] }}><MemoryRouter initialEntries={["/wirtschaftsplaene/new"]}><Routes><Route path="/wirtschaftsplaene/:planId" element={<EconomicPlanDetailPage />} /></Routes></MemoryRouter></AuthCtx.Provider>);
}
function mocks(plan: object = draft) {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans/new") return plan as never;
    return [] as never;
  });
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("rehydrates uncomputed stored rows and reserve on reload and computes exact category IDs/strings", async () => {
  mocks(); const post = vi.spyOn(api, "post").mockResolvedValue(draft);
  let view = show(); await screen.findByDisplayValue("7100.10");
  expect(screen.getByRole("option", { name: /OLD Historische Heizung/ })).toBeDisabled();
  expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("6200.20");
  view.unmount(); view = show(); await screen.findByDisplayValue("7100.10");
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  await screen.findByRole("button", { name: "Berechnen" });
  expect(post).toHaveBeenCalledWith("/api/economic-plans/new/compute", { planned_costs: costs, reserve_contribution_total: "6200.20" });
  view.unmount();
});
it("does not overwrite edits with action response draft_inputs", async () => {
  mocks(); vi.spyOn(api, "post").mockResolvedValue(draft); show();
  const amount = await screen.findByDisplayValue("7100.10"); fireEvent.change(amount, { target: { value: "7200.99" } });
  fireEvent.change(screen.getByLabelText("Rücklagenzuführung (Jahr)"), { target: { value: "6300.99" } });
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" })); await screen.findByRole("button", { name: "Berechnen" });
  expect(screen.getByDisplayValue("7200.99")).toBeInTheDocument(); expect(screen.getByDisplayValue("6300.99")).toBeInTheDocument();
});
it("keeps manual drafts without stored proposals empty", async () => {
  const { draft_inputs: _unused, ...manual } = draft; void _unused; mocks(manual); show();
  await screen.findByText("Geplante Kosten"); expect(screen.queryByDisplayValue("7100.10")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("");
});
it("hydrates the next plan on route changes without leaking active edits", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans/new") return draft as never;
    if (path === "/api/economic-plans/next") return { ...draft, id: "next", plan_year: 2028, draft_inputs: { planned_costs: [{ ...costs[0], planned_amount: "8100.01" }], reserve_contribution_total: "6400.10" } } as never;
    return [] as never;
  });
  render(<AuthCtx.Provider value={auth}><MemoryRouter initialEntries={["/wirtschaftsplaene/new"]}><Link to="/wirtschaftsplaene/next">Nächster Plan</Link><Routes><Route path="/wirtschaftsplaene/:planId" element={<EconomicPlanDetailPage />} /></Routes></MemoryRouter></AuthCtx.Provider>);
  const amount = await screen.findByDisplayValue("7100.10");
  expect(screen.getByText(/Kategorie-ID: historical-id/)).toBeInTheDocument();
  fireEvent.change(amount, { target: { value: "7777.77" } }); fireEvent.click(screen.getByRole("link", { name: "Nächster Plan" }));
  await screen.findByDisplayValue("8100.01"); expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("6400.10");
  expect(screen.queryByDisplayValue("7777.77")).not.toBeInTheDocument();
});
it("does not expose draft write controls to read-only sessions", async () => {
  mocks(); show("READ_ONLY"); await screen.findByRole("heading", { name: /Wirtschaftsplan 2027/ });
  expect(screen.queryByRole("button", { name: "Berechnen" })).not.toBeInTheDocument(); expect(screen.queryByRole("button", { name: "Finalisieren" })).not.toBeInTheDocument();
});

const advanceLines = [
  { id: "line-02", owner_profile_id: "owner", unit_id: "unit-02", planned_amount: "900.00", monthly_amount: "75.00", distribution_base_share: "750", distribution_base_total: "1000" },
  { id: "line-01", owner_profile_id: "owner", unit_id: "unit-01", planned_amount: "300.00", monthly_amount: "25.00", distribution_base_share: "250", distribution_base_total: "1000" },
];
function advanceMocks(status: string, units: object[] | Error) {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/economic-plans/new") return { ...draft, status, lines: advanceLines } as never;
    if (path === "/api/units?property_id=p") {
      if (units instanceof Error) throw units;
      return units as never;
    }
    if (path === "/api/owner-profiles") return [{ id: "owner", person_id: "person" }] as never;
    if (path === "/api/persons") return [{ id: "person", first_name: "Friedrich-Wilhelm", last_name: "Muster-Beispielhausen" }] as never;
    return [] as never;
  });
}
it.each(["draft", "finalized"])("associates each unit with its own stored advance amounts in the %s view", async status => {
  advanceMocks(status, [
    { id: "unit-01", number: "01", location: "Vorderhaus" },
    { id: "unit-02", number: "02", location: "Hinterhaus" },
  ]);
  show();
  for (const [label, annual, monthly] of [["01 – Vorderhaus", "300.00 EUR", "25.00 EUR"], ["02 – Hinterhaus", "900.00 EUR", "75.00 EUR"]]) {
    const labelCell = await screen.findByText(`Einheit ${label}`, { exact: true });
    const row = within(labelCell.closest("tr")!);
    expect(row.getByText("Friedrich-Wilhelm Muster-Beispielhausen")).toBeInTheDocument();
    expect(row.getByText(annual, { exact: true })).toBeInTheDocument();
    expect(row.getByText(monthly, { exact: true })).toBeInTheDocument();
  }
});
it.each([
  ["absent lookup data", []],
  ["empty current number", [{ id: "unit-01", number: "", location: "" }]],
  ["lookup failure", new Error("Synthetic lookup failure")],
])("keeps stored amounts visible with an explicit unit-label fallback for %s", async (_reason, units) => {
  advanceMocks("finalized", units as object[] | Error); show();
  const fallback = await screen.findByText("Einheit (Bezeichnung nicht verfügbar; ID: unit-01)", { exact: true });
  expect(within(fallback.closest("tr")!).getByText("300.00 EUR", { exact: true })).toBeInTheDocument();
});
it("distinguishes an absent unit reference from an unavailable current label", async () => {
  mocks({ ...draft, lines: [{ ...advanceLines[0], unit_id: null }] }); show();
  const fallback = await screen.findByText("Einheit nicht zugeordnet", { exact: true });
  expect(within(fallback.closest("tr")!).getByText("900.00 EUR", { exact: true })).toBeInTheDocument();
});

it.each([
  [new ApiError(404, "Wirtschaftsplan nicht gefunden."), "Wirtschaftsplan nicht gefunden."],
  [new ApiError(401, "Unauthorized"), "Nicht authentifiziert."],
  [new Error("Synthetic network failure"), "Error: Synthetic network failure"],
])("renders load errors without the API class prefix and preserves auth and fallback: %s", async (error, expected) => {
  const get = vi.spyOn(api, "get").mockRejectedValue(error);
  try {
    show();
    const rendered = await screen.findByText(expected, { exact: true });
    expect(rendered.firstChild?.textContent?.trim()).toBe(expected);
    expect(screen.queryByText(/ApiError:/)).not.toBeInTheDocument();
  } finally { get.mockRestore(); }
});
