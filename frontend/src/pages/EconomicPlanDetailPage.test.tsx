import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { api } from "../api/client";
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
