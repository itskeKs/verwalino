import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, ApiError } from "../api/client";
import SpecialLeviesPage from "./SpecialLeviesPage";
import { createSpecialLevy, listSpecialLevies } from "../api/endpoints";

const auth = vi.hoisted(() => ({ role: "CLERK" }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: auth.role } }) }));
vi.mock("../components/AccountingScopeSelector", () => ({ AccountingScopeSelector: (p: { propertyId?: string; capability: string; onChange: (id: string | null) => void }) => <select aria-label={`Abrechnungskreis ${p.capability}`} onChange={e => p.onChange(e.target.value || null)}><option value="">Kreis wählen</option><option value={`scope-${p.propertyId}`}>WEG Gesamtobjekt</option><option value="scope-other">Weiterer WEG-Kreis</option></select> }));
const levy = { id: "levy", property_id: "p1", accounting_scope_id: "scope-p1", amount: "10000.00", due_date: "2026-06-15", resolution_id: null, allocations: [
  { owner_profile_id: "o1", unit_id: "u1", mea: "300", amount: "3000.00" },
  { owner_profile_id: "o2", unit_id: "u2", mea: "300", amount: "3000.00" },
  { owner_profile_id: "o3", unit_id: "u3", mea: "400", amount: "4000.00" },
] };
let getRows: () => Promise<typeof levy[]>;
beforeEach(() => {
  auth.role = "CLERK"; getRows = async () => [];
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url === "/api/properties") return [{ id: "p1", name: "Sonnenhof", management_type: "WEG" }, { id: "p2", name: "Parkhof", management_type: "WEG" }];
    if (url.startsWith("/api/units")) return [1, 2, 3].map(n => ({ id: `u${n}`, property_id: "p1", number: String(n) }));
    if (url === "/api/owner-profiles") return [1, 2, 3].map(n => ({ id: `o${n}`, person_id: `person${n}` }));
    if (url === "/api/persons") return [1, 2, 3].map(n => ({ id: `person${n}`, first_name: "Ada", last_name: `Besitzer ${n}` }));
    if (url.startsWith("/api/special-levies?")) return getRows();
    throw new Error(`Unexpected GET ${url}`);
  });
  vi.spyOn(api, "post").mockResolvedValue(levy);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function show() { return render(<MemoryRouter><SpecialLeviesPage /></MemoryRouter>); }
async function choose() {
  await screen.findByRole("option", { name: "Sonnenhof" });
  fireEvent.change(screen.getByLabelText("WEG-Objekt *"), { target: { value: "p1" } });
  fireEvent.change(screen.getByLabelText("Abrechnungskreis read"), { target: { value: "scope-p1" } });
  await screen.findByText("Keine Sonderumlagen vorhanden.");
  if (auth.role === "CLERK" || auth.role === "OWNER_ADMIN") fireEvent.change(screen.getByLabelText("Abrechnungskreis prepare"), { target: { value: "scope-p1" } });
}
function fill(amount = "10000,00", date = "2026-06-15") {
  fireEvent.change(screen.getByLabelText("Betrag (EUR) *"), { target: { value: amount } });
  fireEvent.change(screen.getByLabelText("Fällig am *"), { target: { value: date } });
}
it("does not issue an unscoped GET, and links existing workflows", async () => {
  show(); await screen.findByRole("option", { name: "Sonnenhof" });
  expect(vi.mocked(api.get).mock.calls.some(([url]) => url.startsWith("/api/special-levies"))).toBe(false);
  expect(screen.getByRole("link", { name: "Eigentümerkonto" })).toHaveAttribute("href", "/mietkonto");
  expect(screen.getByRole("link", { name: "Mahnungen" })).toHaveAttribute("href", "/mahnungen");
});
it("creates an exact decimal payload and displays only frozen server shares with human labels", async () => {
  show(); await choose(); fill(); fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" }));
  await screen.findByText("Ada Besitzer 1");
  expect(api.post).toHaveBeenCalledWith("/api/special-levies", { property_id: "p1", accounting_scope_id: "scope-p1", amount: "10000.00", due_date: "2026-06-15" });
  expect(screen.getAllByText("3000.00 EUR")).toHaveLength(2);
  expect(screen.getByText("4000.00 EUR")).toBeVisible();
  expect(screen.getByText("Wohnung 3")).toBeVisible();
});
it.each(["READ_ONLY", "UNKNOWN"])("%s cannot save but can list in READ scope", async role => {
  auth.role = role; show(); await choose();
  expect(screen.queryByLabelText("Betrag (EUR) *")).toBeNull();
  expect(screen.queryByRole("button", { name: "Sonderumlage anlegen" })).toBeNull();
});
it.each(["0", "-1", "1e4", "1.001", "1000000000000", "1.000,00", "NaN"])("rejects invalid amount %s", async amount => {
  show(); await choose(); fill(amount); fireEvent.submit(screen.getByRole("button", { name: "Sonderumlage anlegen" }).closest("form")!);
  expect(await screen.findByRole("alert")).toHaveTextContent(/Betrag/); expect(api.post).not.toHaveBeenCalled();
});
it("accepts the Numeric(14,2) maximum without float conversion", async () => {
  show(); await choose(); fill("999999999999,99"); fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/special-levies", expect.objectContaining({ amount: "999999999999.99" })));
});
it.each([401, 403, 404, 409, 422])("retains inputs after HTTP %s and permits retry", async status => {
  vi.mocked(api.post).mockRejectedValueOnce(new ApiError(status, "Speichern fehlgeschlagen"));
  show(); await choose(); fill(); fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Speichern fehlgeschlagen");
  expect(screen.getByLabelText("Betrag (EUR) *")).toHaveValue("10000,00");
  fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" })); await screen.findByText("Ada Besitzer 1");
});
it("prevents duplicate submits and a late list cannot erase created shares", async () => {
  let resolveList!: (value: typeof levy[]) => void; let resolvePost!: (value: typeof levy) => void;
  show(); await choose();
  getRows = () => new Promise(resolve => { resolveList = resolve; });
  fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
  vi.mocked(api.post).mockImplementation(() => new Promise(resolve => { resolvePost = resolve; }));
  fill(); const form = screen.getByRole("button", { name: "Sonderumlage anlegen" }).closest("form")!;
  fireEvent.submit(form); fireEvent.submit(form); expect(api.post).toHaveBeenCalledTimes(1);
  await act(async () => { resolvePost(levy); }); await screen.findByText("Ada Besitzer 1");
  await act(async () => { resolveList([]); }); expect(screen.getByText("Ada Besitzer 1")).toBeVisible();
});
it("resets scopes and results on property changes, ignoring old requests", async () => {
  let resolve!: (value: typeof levy[]) => void;
  show(); await choose(); getRows = () => new Promise(r => { resolve = r; });
  fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" }));
  fireEvent.change(screen.getByLabelText("WEG-Objekt *"), { target: { value: "p2" } });
  await act(async () => { resolve([levy]); }); expect(screen.queryByText("Ada Besitzer 1")).toBeNull();
  expect(screen.getByRole("button", { name: "Sonderumlage anlegen" })).toBeDisabled();
});
it("retries a failed list request", async () => {
  show(); await choose(); getRows = async () => { throw new Error("Netzwerkfehler"); };
  fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" })); await screen.findByText("Netzwerkfehler");
  getRows = async () => [levy]; fireEvent.click(screen.getByRole("button", { name: "Aktualisieren" })); await screen.findByText("Ada Besitzer 1");
});
it("sanitizes extra fields and URL-encodes endpoint filters", async () => {
  const body = { property_id: "p1", accounting_scope_id: "scope-p1", amount: "10000.00", due_date: "2026-06-15", allocations: levy.allocations };
  await createSpecialLevy(body);
  expect(api.post).toHaveBeenCalledWith("/api/special-levies", { property_id: "p1", accounting_scope_id: "scope-p1", amount: "10000.00", due_date: "2026-06-15" });
  await listSpecialLevies("scope&1", "property/1");
  expect(api.get).toHaveBeenCalledWith("/api/special-levies?accounting_scope_id=scope%261&property_id=property%2F1");
});


it("retains inputs after a network failure", async () => {
  vi.mocked(api.post).mockRejectedValueOnce(new Error("Netzwerk unterbrochen"));
  show(); await choose(); fill(); fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Netzwerk unterbrochen");
  expect(screen.getByLabelText("Fällig am *")).toHaveValue("2026-06-15");
  expect(screen.getByRole("button", { name: "Sonderumlage anlegen" })).toBeEnabled();
});
it("rejects an invalid due date without imposing a future-only restriction", async () => {
  show(); await choose(); fill("1", "");
  fireEvent.submit(screen.getByRole("button", { name: "Sonderumlage anlegen" }).closest("form")!);
  expect(await screen.findByRole("alert")).toHaveTextContent("gültiges Fälligkeitsdatum");
  expect(api.post).not.toHaveBeenCalled();
  fill("0,01", "2020-02-29"); fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" }));
  await waitFor(() => expect(api.post).toHaveBeenCalledWith("/api/special-levies", expect.objectContaining({ amount: "0.01", due_date: "2020-02-29" })));
});
it("ignores a completed creation after switching the property", async () => {
  let finish!: (value: typeof levy) => void;
  vi.mocked(api.post).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  show(); await choose(); fill(); fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" }));
  fireEvent.change(screen.getByLabelText("WEG-Objekt *"), { target: { value: "p2" } });
  await act(async () => { finish(levy); });
  expect(screen.queryByText("Ada Besitzer 1")).toBeNull();
  expect(screen.getByLabelText("WEG-Objekt *")).toHaveValue("p2");
});
it("ignores late creation results after unmount", async () => {
  let finish!: (value: typeof levy) => void;
  vi.mocked(api.post).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = show(); await choose(); fill(); fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" }));
  view.unmount(); await act(async () => { finish(levy); }); expect(api.post).toHaveBeenCalledTimes(1);
});
it("preserves newly created shares when switching the listing to the preparation scope", async () => {
  const created = { ...levy, accounting_scope_id: "scope-other" };
  vi.mocked(api.post).mockResolvedValue(created);
  show(); await choose();
  const prepare = screen.getByLabelText("Abrechnungskreis prepare");
  fireEvent.change(prepare, { target: { value: "scope-other" } });
  fill(); fireEvent.click(screen.getByRole("button", { name: "Sonderumlage anlegen" }));
  await waitFor(() => expect(api.post).toHaveBeenCalled());
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/api/special-levies?accounting_scope_id=scope-other&property_id=p1"));
  await act(async () => {});
  expect(screen.getByText("Ada Besitzer 1")).toBeVisible();
});
