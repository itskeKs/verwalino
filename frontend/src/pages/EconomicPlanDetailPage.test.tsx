import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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
it.each([1, 3, 28, 31, null])("shows resolved due day %s without changing historical values", async day => {
  mocks({ ...draft, monthly_due_day: day }); show();
  expect(await screen.findByText(`Fälligkeit: jeweils zum ${day ?? 1}. des Monats`)).toBeVisible();
});
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
it("keeps the stored reserve and leaves cost rows empty in a new manual draft", async () => {
  mocks({ ...draft, draft_inputs: null }); show();
  await screen.findByText("Geplante Kosten");
  expect(screen.queryByLabelText("Kategorie 1")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("6200.20");
});
it.each(["7200.000", "7200.", " 7200.00 "])("computes a new zero-reserve draft with cost %j", async (enteredAmount) => {
  const manual = { ...draft, reserve_contribution_total: "0.00", draft_inputs: null };
  mocks(manual);
  const post = vi.spyOn(api, "post").mockResolvedValue({
    ...manual, lines: [{ id: "advance", owner_profile_id: "owner", planned_amount: "7200.00", monthly_amount: "600.00" }],
    draft_inputs: { reserve_contribution_total: "0", planned_costs: [{
      category_code: "", label: "", planned_amount: "7200", distribution_key: "MEA", cost_group: "UMLAGEFAEHIG", category_id: null,
    }] },
  });
  show();
  expect(await screen.findByRole("cell", { name: "0.00 EUR" })).toBeVisible();
  expect(screen.getByLabelText("Rücklagenzuführung (Jahr)")).toHaveValue("0.00");
  fireEvent.click(screen.getByRole("button", { name: "+ Position" }));
  fireEvent.change(screen.getByLabelText("Position – Jahresbetrag"), { target: { value: enteredAmount } });
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  expect(await screen.findByRole("cell", { name: "600.00 EUR" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Finalisieren" })).toBeEnabled();
  expect(post).toHaveBeenCalledWith("/api/economic-plans/new/compute", expect.objectContaining({
    reserve_contribution_total: "0.00",
    planned_costs: [expect.objectContaining({ planned_amount: enteredAmount })],
  }));
});
it.each([
  ["Berechnen", ""], ["Berechnen", "kein Betrag"], ["Berechnen", "-1"], ["Finalisieren", ""],
])("rejects %s with reserve '%s' and preserves edited costs", async (button, invalid) => {
  mocks();
  const post = vi.spyOn(api, "post").mockResolvedValue(draft);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  show();
  const amount = await screen.findByDisplayValue("7100.10");
  fireEvent.change(amount, { target: { value: "7200.00" } });
  const reserve = screen.getByLabelText("Rücklagenzuführung (Jahr)");
  fireEvent.change(reserve, { target: { value: invalid } });
  fireEvent.click(screen.getByRole("button", { name: button }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Bitte geben Sie die Rücklagenzuführung als gültigen EUR-Betrag ein.");
  expect(reserve).toHaveAttribute("aria-invalid", "true");
  expect(reserve).toHaveAccessibleDescription("Bitte geben Sie die Rücklagenzuführung als gültigen EUR-Betrag ein.");
  expect(amount).toHaveValue("7200.00");
  expect(post).not.toHaveBeenCalled();
  expect(confirm).not.toHaveBeenCalled();
  fireEvent.change(reserve, { target: { value: "0,00" } });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: button }));
  await screen.findByRole("button", { name: "Berechnen" });
  expect(post).toHaveBeenCalledWith(
    `/api/economic-plans/new/${button === "Berechnen" ? "compute" : "finalize"}`,
    { planned_costs: [{ ...costs[0], planned_amount: "7200.00" }], reserve_contribution_total: "0.00" },
  );
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
    if (path === "/api/economic-plans/new") return {
      ...draft, status, lines: advanceLines, monthly_due_day: 28,
      addressees: status === "finalized" ? [{ unit_id: "unit-01", unit_number: "01",
        owner_profile_id: "historical-owner", owner_name: "Historischer Empfänger", key_date: "2027-01-01" }] : [],
    } as never;
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
  expect(await screen.findByText("Fälligkeit: jeweils zum 28. des Monats")).toBeVisible();
  if (status === "finalized") {
    expect(screen.getByRole("region", { name: "Dokumentempfänger" })).toHaveTextContent("Einheit 01: Historischer Empfänger");
  }
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

it("shows the frozen plan addressee and the plan-start date", async () => {
  mocks({ ...draft, status: 'finalized', addressees: [{ unit_id: 'unit-2', unit_number: '2',
    owner_profile_id: 'karl', owner_name: 'Karl Weiß', key_date: '2026-01-01' }] });
  show();
  const recipients = await screen.findByRole('region', { name: 'Dokumentempfänger' });
  expect(recipients).toHaveTextContent('Einheit 2: Karl Weiß');
  expect(recipients).toHaveTextContent('Eigentümer am 01.01.2026');
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

it("requires updated results before finalizing edits to a calculated draft", async () => {
  const advance = { id: "advance", owner_profile_id: "owner", planned_amount: "7200.00", monthly_amount: "600.00" };
  const savedCosts = [{ ...costs[0], planned_amount: "7200" }];
  const computed = { ...draft, lines: [advance], draft_inputs: { ...draft.draft_inputs, planned_costs: savedCosts } };
  mocks(computed);
  vi.spyOn(api, "post").mockResolvedValue({
    ...computed,
    lines: [{ ...advance, planned_amount: "8400.00", monthly_amount: "700.00" }],
    draft_inputs: { ...computed.draft_inputs, planned_costs: [{ ...savedCosts[0], planned_amount: "8400" }] },
  });
  show();
  expect(await screen.findByRole("cell", { name: "600.00 EUR" })).toBeVisible();
  const finalize = screen.getByRole("button", { name: "Finalisieren" });
  const amount = screen.getByRole("textbox", { name: "Historische Heizung – Jahresbetrag" });
  fireEvent.change(amount, { target: { value: "7200.00" } });
  expect(finalize).toBeEnabled();

  fireEvent.change(amount, { target: { value: "7200.001" } });
  expect(finalize).toBeDisabled();

  fireEvent.change(amount, { target: { value: "8400.00" } });
  expect(screen.queryByRole("cell", { name: "600.00 EUR" })).not.toBeInTheDocument();
  expect(finalize).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  expect(await screen.findByRole("cell", { name: "700.00 EUR" })).toBeVisible();
  expect(finalize).toBeEnabled();
});

it("keeps an edit made during computation dirty when the result arrives", async () => {
  const computed = {
    ...draft,
    lines: [{ id: "advance", owner_profile_id: "owner", planned_amount: "7200.00", monthly_amount: "600.00" }],
    draft_inputs: { ...draft.draft_inputs, planned_costs: [{ ...costs[0], planned_amount: "7200" }] },
  };
  let finish!: (response: typeof computed) => void;
  mocks();
  vi.spyOn(api, "post").mockReturnValue(new Promise<typeof computed>(resolve => { finish = resolve; }));
  show();
  const amount = await screen.findByDisplayValue("7100.10");
  fireEvent.change(amount, { target: { value: "7200." } });
  fireEvent.click(screen.getByRole("button", { name: "Berechnen" }));
  fireEvent.change(amount, { target: { value: "8400.00" } });
  await act(async () => finish(computed));

  const finalize = screen.getByRole("button", { name: "Finalisieren" });
  expect(amount).toHaveValue("8400.00");
  expect(screen.queryByRole("cell", { name: "600.00 EUR" })).not.toBeInTheDocument();
  expect(finalize).toBeDisabled();

  fireEvent.change(amount, { target: { value: "7200." } });
  expect(screen.getByRole("cell", { name: "600.00 EUR" })).toBeVisible();
  expect(finalize).toBeEnabled();
});
