import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { AuthCtx, type AuthContextValue } from "../auth/useAuth";
import { PreviousPlan } from "./PreviousPlan";

vi.mock("./AccountingScopeSelector", () => ({ AccountingScopeSelector: (p: { propertyId: string; capability: string; kind: string; value: string | null; onChange: (id: string | null) => void; onValidityChange: (valid: boolean) => void }) => <>
  <select aria-label={`scope-${p.capability}`} data-property={p.propertyId} data-kind={p.kind} value={p.value ?? ""} onChange={e => p.onChange(e.target.value || null)}><option value="">legacy</option><option value="prior">prior</option><option value="future">future</option></select>
  <button onClick={() => p.onValidityChange(false)}>invalidate-{p.capability}</button>
</> }));
const properties = [{ id: "p", name: "WEG Haus", management_type: "WEG" }, { id: "m", name: "Misch Haus", management_type: "MISCH" }, { id: "rent", name: "Miet Haus", management_type: "MIET" }];
const proposal = { source_year: 2026, plan_year: 2027, period_start: "2026-01-01", period_end: "2026-12-31", planned_costs: [{ category_id: "historical", category_code: "100", label: "Heizung", planned_amount: "7000.00", distribution_key: "MEA", cost_group: "UMLAGEFAEHIG" }], reserve_contribution_total: "6000.00", source_digest: "digest", footnote: "Bestätigte Ist-Kosten, Gutschriften verrechnet." };
let auth: AuthContextValue;
let get: ReturnType<typeof vi.spyOn>;
let post: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  auth = { session: { organization_id: "org", user_id: "writer", role: "CLERK" } as AuthContextValue["session"], loading: false, login: vi.fn(), logout: vi.fn() };
  get = vi.spyOn(api, "get").mockResolvedValue({ requires_accounting_scope: false });
  post = vi.spyOn(api, "post").mockImplementation(async path => path.endsWith("/preview") ? proposal : { id: "new" });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function app(props = properties) { return <AuthCtx.Provider value={auth}><MemoryRouter><Routes><Route path="/" element={<PreviousPlan properties={props} />} /><Route path="/wirtschaftsplaene/:id" element={<h1>Neuer Detailplan</h1>} /></Routes></MemoryRouter></AuthCtx.Provider>; }
async function open() { fireEvent.click(screen.getByRole("button", { name: "Plan aus Vorjahres-Ist" })); await screen.findByText(/Nur ein Vorschlag/); }
async function select() {
  await open();
  fireEvent.change(screen.getByLabelText("Objekt für Vorjahresplan"), { target: { value: "p" } });
  fireEvent.change(screen.getByLabelText("Zieljahr"), { target: { value: "2027" } });
}
async function preview() { await select(); fireEvent.click(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" })); await screen.findByDisplayValue("7000.00"); }
function create() { fireEvent.click(screen.getByRole("button", { name: "Plan aus Vorjahr anlegen" })); const dialog = screen.getByRole("dialog"); fireEvent.click(within(dialog).getByRole("button", { name: "Plan aus Vorjahr anlegen" })); }
it("starts closed without requests and hides the flow for readers", () => {
  const view = render(app()); expect(get).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Zieljahr")).not.toBeInTheDocument();
  auth = { ...auth, session: { ...auth.session!, role: "READ_ONLY" } as AuthContextValue["session"] };
  view.rerender(app()); expect(screen.queryByRole("button", { name: "Plan aus Vorjahres-Ist" })).not.toBeInTheDocument();
});
it("uses separate read/prepare WEG scope groups and explicit preview", async () => {
  render(app()); await select(); expect(post).not.toHaveBeenCalled();
  expect(screen.queryByRole("option", { name: "Miet Haus" })).not.toBeInTheDocument();
  const read = within(screen.getByRole("group", { name: "Vorjahres-Ist (Lesekreis)" })).getByLabelText("scope-read");
  const prepare = within(screen.getByRole("group", { name: "Neuer Plan (Vorbereitungskreis)" })).getByLabelText("scope-prepare");
  expect(read).toHaveAttribute("data-property", "p"); expect(prepare).toHaveAttribute("data-kind", "WEG");
  fireEvent.change(read, { target: { value: "prior" } }); fireEvent.change(prepare, { target: { value: "future" } });
  fireEvent.click(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" }));
  await screen.findByDisplayValue("7000.00");
  expect(post.mock.calls[0][1]).toEqual({ property_id: "p", plan_year: 2027, source_accounting_scope_id: "prior", accounting_scope_id: "future" });
  expect(screen.getByText(/2026-01-01/)).toBeInTheDocument(); expect(screen.getByText("MEA")).toBeInTheDocument();
  expect(screen.getByText(/Kosten und Rücklage: 13000.00 EUR/)).toBeInTheDocument();
});
it("edits only amounts and reserve, confirms once and navigates to the returned ID", async () => {
  render(app()); await preview();
  fireEvent.change(screen.getByLabelText("Heizung – Jahresbetrag"), { target: { value: "7100,10" } });
  fireEvent.change(screen.getByLabelText("Rücklagenzuführung – Vorschlag"), { target: { value: "6200.20" } });
  expect(screen.getByText(/13300.30 EUR/)).toBeInTheDocument(); create();
  await screen.findByText("Neuer Detailplan");
  expect(post.mock.calls[1][1]).toEqual({ property_id: "p", plan_year: 2027, expected_source_digest: "digest", planned_costs: [{ ...proposal.planned_costs[0], planned_amount: "7100.10" }], reserve_contribution_total: "6200.20" });
  expect(post).toHaveBeenCalledTimes(2);
});
it.each(["", "-1", "0.001", "Infinity", "NaN"])("blocks invalid edited amounts %s", async value => {
  render(app()); await preview(); fireEvent.change(screen.getByLabelText("Heizung – Jahresbetrag"), { target: { value } });
  expect(screen.getByRole("button", { name: "Plan aus Vorjahr anlegen" })).toBeDisabled(); expect(screen.getByRole("alert")).toHaveTextContent(/Beträge/);
});
it.each(["2027.5", "1", "9999", ""])("blocks noninteger/out of range year %s", async value => {
  render(app()); await select(); fireEvent.change(screen.getByLabelText("Zieljahr"), { target: { value } });
  expect(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" })).toBeDisabled();
});
it.each(["year", "property", "read", "prepare", "validity", "props"])("invalidates proposal on %s changes", async change => {
  const view = render(app()); await preview();
  if (change === "year") fireEvent.change(screen.getByLabelText("Zieljahr"), { target: { value: "2028" } });
  if (change === "property") fireEvent.change(screen.getByLabelText("Objekt für Vorjahresplan"), { target: { value: "m" } });
  if (change === "read" || change === "prepare") fireEvent.change(screen.getByLabelText(`scope-${change}`), { target: { value: "future" } });
  if (change === "validity") fireEvent.click(screen.getByText("invalidate-read"));
  if (change === "props") view.rerender(app(properties.filter(p => p.id !== "p")));
  expect(screen.queryByDisplayValue("7000.00")).not.toBeInTheDocument(); expect(post).toHaveBeenCalledTimes(1);
});
it.each([403, 422, 409])("clears proposal after definitive create error %s", async status => {
  render(app()); await preview(); post.mockRejectedValueOnce(new ApiError(status, "Serverfehler")); create();
  expect(await screen.findByRole("alert")).toHaveTextContent(status === 409 ? /erneut prüfen/ : /Serverfehler/);
  expect(screen.queryByDisplayValue("7000.00")).not.toBeInTheDocument(); expect(post).toHaveBeenCalledTimes(2);
});
it.each([new ApiError(500, "Serverfehler"), new TypeError("Failed to fetch")])("locks uncertain creation without retries", async error => {
  render(app()); await preview(); post.mockRejectedValueOnce(error); create();
  expect(await screen.findByRole("alert")).toHaveTextContent(/Planliste prüfen/);
  expect(screen.getByLabelText("Zieljahr")).toBeDisabled(); expect(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" })).toBeDisabled(); expect(post).toHaveBeenCalledTimes(2);
});
it("blocks unknown policy and requires both scopes under scoped policy", async () => {
  get.mockRejectedValueOnce(new ApiError(403, "Policy nicht zugänglich")); render(app()); await select();
  expect(await screen.findByRole("alert")).toHaveTextContent("Policy nicht zugänglich");
  expect(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" })).toBeDisabled();
  get.mockResolvedValueOnce({ requires_accounting_scope: true }); fireEvent.click(screen.getByText("Berechtigung erneut prüfen"));
  await act(async () => {}); expect(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("scope-read"), { target: { value: "prior" } });
  expect(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("scope-prepare"), { target: { value: "future" } });
  expect(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" })).toBeEnabled();
});
it.each(["year", "unmount", "session"])("ignores late preview after %s", async change => {
  let resolve!: (value: unknown) => void;
  post.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = render(app()); await select(); const button = screen.getByRole("button", { name: "Vorjahres-Ist prüfen" });
  fireEvent.click(button); fireEvent.click(button); expect(post).toHaveBeenCalledTimes(1);
  if (change === "year") fireEvent.change(screen.getByLabelText("Zieljahr"), { target: { value: "2028" } });
  if (change === "unmount") view.unmount();
  if (change === "session") { auth = { ...auth, session: { ...auth.session!, user_id: "other" } }; view.rerender(app()); }
  await act(async () => resolve(proposal)); expect(screen.queryByDisplayValue("7000.00")).not.toBeInTheDocument();
});
it.each([403, 422])("shows preview rejection %s without creating anything", async status => {
  render(app()); await select(); post.mockRejectedValueOnce(new ApiError(status, "Kein Zugriff oder ungültige Quelle"));
  fireEvent.click(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(`HTTP ${status}`);
  expect(screen.queryByRole("button", { name: "Plan aus Vorjahr anlegen" })).not.toBeInTheDocument(); expect(post).toHaveBeenCalledTimes(1);
});
it.each(["", "-0.01", "6000.001", "Infinity"])("blocks invalid reserve %s", async value => {
  render(app()); await preview(); fireEvent.change(screen.getByLabelText("Rücklagenzuführung – Vorschlag"), { target: { value } });
  expect(screen.getByRole("button", { name: "Plan aus Vorjahr anlegen" })).toBeDisabled(); expect(post).toHaveBeenCalledTimes(1);
});
it.each([
  { ...proposal, reserve_contribution_total: "NaN" },
  { ...proposal, planned_costs: [{ ...proposal.planned_costs[0], planned_amount: "-1.00" }] },
  { ...proposal, planned_costs: [{ ...proposal.planned_costs[0], planned_amount: "1.001" }] },
  { ...proposal, source_year: 2025 },
  { ...proposal, planned_costs: [proposal.planned_costs[0], proposal.planned_costs[0]] },
])("rejects malformed server proposal %#", async malformed => {
  render(app()); await select(); post.mockResolvedValueOnce(malformed);
  fireEvent.click(screen.getByRole("button", { name: "Vorjahres-Ist prüfen" })); await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Plan aus Vorjahr anlegen" })).not.toBeInTheDocument();
});
it.each(["session", "unmount", "props"])("ignores late creation after %s without another POST", async change => {
  const view = render(app()); await preview(); let resolve!: (value: unknown) => void;
  post.mockImplementationOnce(() => new Promise(r => { resolve = r; })); create();
  if (change === "session") { auth = { ...auth, session: { ...auth.session!, user_id: "other" } }; view.rerender(app()); }
  if (change === "unmount") view.unmount();
  if (change === "props") view.rerender(app(properties.filter(p => p.id !== "p")));
  await act(async () => resolve({ id: "new" }));
  expect(screen.queryByText("Neuer Detailplan")).not.toBeInTheDocument(); expect(post).toHaveBeenCalledTimes(2);
  if (change !== "unmount") { expect(screen.getByRole("link", { name: "Planliste prüfen" })).toBeInTheDocument(); expect(screen.getByLabelText("Zieljahr")).toBeDisabled(); }
});
it("guards duplicate create clicks while the response is pending", async () => {
  render(app()); await preview(); let resolve!: (value: unknown) => void;
  post.mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  fireEvent.click(screen.getByRole("button", { name: "Plan aus Vorjahr anlegen" }));
  const button = within(screen.getByRole("dialog")).getByRole("button", { name: "Plan aus Vorjahr anlegen" });
  fireEvent.click(button); fireEvent.click(button); expect(post).toHaveBeenCalledTimes(2);
  await act(async () => resolve({ id: "new" })); await screen.findByText("Neuer Detailplan");
});

it("accepts prior actuals for an April property period", async () => {
  post.mockResolvedValueOnce({ ...proposal, period_start: "2026-04-01", period_end: "2027-03-31" });
  render(app()); await preview();
  expect(screen.getByDisplayValue("7000.00")).toBeVisible();
  expect(screen.getByRole("button", { name: "Plan aus Vorjahr anlegen" })).toBeEnabled();
});
