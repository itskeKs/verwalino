import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import * as endpoints from "../api/endpoints";
import { PropertyDetailPage } from "./PropertyDetailPage";
import { UnitDetailPage } from "./UnitDetailPage";
import { PersonDetailPage } from "./PersonDetailPage";

vi.mock("../components/AttachmentsPanel", () => ({ AttachmentsPanel: () => null }));
vi.mock("../components/MeterPanel", () => ({ MeterPanel: () => null }));
vi.mock("../components/DsgvoPanel", () => ({ DsgvoPanel: () => null }));
vi.mock("../api/endpoints", async importOriginal => ({
  ...await importOriginal<typeof endpoints>(),
  getProperty: vi.fn(), getPropertyKnowledge: vi.fn(), listUnits: vi.fn(), getUnit: vi.fn(),
  getPerson: vi.fn(), listOwnership: vi.fn(), listLeases: vi.fn(), listOwnerProfiles: vi.fn(),
  listTenantProfiles: vi.fn(), listPersons: vi.fn(), listVendors: vi.fn(), listLeaseRentChanges: vi.fn(),
}));
const session = { user_id: "user", organization_id: "org", role: "CLERK" };
const property = { id: "property", name: "Testhaus", management_type: "MIET", fiscal_year_start_month: 1 };
const unit = { id: "unit", property_id: "property", number: "42" };
const person = { id: "person", first_name: "Ada", last_name: "Test", can_prepare: true };
const lease = { id: "lease", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-01-01", valid_to: "2025-12-31", base_rent: "700", advance_payment: "150", heating_advance_payment: "0.00", deposit: null, notes: "Historie" };
let fetchMock: ReturnType<typeof vi.fn>;
let leases: typeof lease[];
function content(path = "/objekte/property/einheiten/unit", role = "CLERK", user = "user") {
  return <AuthCtx.Provider value={{ loading: false, session: { ...session, user_id: user, role }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={[path]}><Link to="/objekte/property/einheiten/other">Andere Einheit</Link><Routes>
      <Route path="/objekte/:propertyId/einheiten/:unitId" element={<UnitDetailPage />} />
      <Route path="/objekte/:propertyId" element={<PropertyDetailPage />} />
      <Route path="/personen/:personId" element={<PersonDetailPage />} />
      <Route path="/objekte" element={<p>Objektübersicht</p>} />
      <Route path="/personen" element={<p>Personenübersicht</p>} />
    </Routes></MemoryRouter>
  </AuthCtx.Provider>;
}
function show(path = "/objekte/property/einheiten/unit", role = "CLERK", user = "user") { return render(content(path, role, user)); }
async function open(label = "Einheit löschen") {
  fireEvent.click(await screen.findByRole("button", { name: label }));
  return screen.findByRole("dialog", { name: label });
}
function confirm() { fireEvent.click(screen.getByRole("button", { name: "Löschen bestätigen" })); }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => {
  vi.resetAllMocks(); leases = [];
  fetchMock = vi.fn(async () => new Response(null, { status: 204 })); vi.stubGlobal("fetch", fetchMock);
  vi.mocked(endpoints.getProperty).mockResolvedValue(property as never);
  vi.mocked(endpoints.getPropertyKnowledge).mockResolvedValue(null as never);
  vi.mocked(endpoints.listUnits).mockResolvedValue([]);
  vi.mocked(endpoints.getUnit).mockImplementation(async id => ({ ...unit, id, number: id === "other" ? "99" : "42" }) as never);
  vi.mocked(endpoints.getPerson).mockResolvedValue(person as never);
  vi.mocked(endpoints.listLeases).mockImplementation(async () => leases);
  for (const fn of [endpoints.listOwnership, endpoints.listOwnerProfiles, endpoints.listTenantProfiles, endpoints.listPersons, endpoints.listVendors, endpoints.listLeaseRentChanges]) vi.mocked(fn).mockResolvedValue([]);
});
it.each([['deleteProperty', 'properties'], ['deleteUnit', 'units'], ['deletePerson', 'persons'], ['deleteLease', 'leases']] as const)("%s sends one encoded DELETE without body and accepts empty 204", async (name, resource) => {
  await endpoints[name]("id /?#");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(fetchMock).toHaveBeenCalledWith(`/api/${resource}/id%20%2F%3F%23`, expect.objectContaining({ method: "DELETE", credentials: "include" }));
  expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
});
it("cancel names the unit and irreversible reference policy, without writing", async () => {
  show(); const dialog = await open();
  expect(dialog).toHaveTextContent("42"); expect(dialog).toHaveTextContent(/unwiderruflich/i); expect(dialog).toHaveTextContent(/Referenzen/);
  fireEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument(); expect(fetchMock).not.toHaveBeenCalled();
});
it.each([403, 404, 409])("keeps unit and canonical detail after %s, with no retry", async status => {
  const reason = "Einheit kann wegen bestehender Mietverträge nicht gelöscht werden.";
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: reason }), { status }));
  leases = [lease]; show(); await open(); confirm();
  expect(await screen.findByRole("alert")).toHaveTextContent(reason);
  expect(screen.getByText("Einheit 42", { selector: "h1" })).toBeInTheDocument();
  expect(screen.getByText("2025-12-31")).toBeInTheDocument(); expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("deletes an empty unit then navigates to its property and remains deleted on reload", async () => {
  let exists = true;
  vi.mocked(endpoints.listUnits).mockImplementation(async () => exists ? [unit as never] : []);
  vi.mocked(endpoints.getUnit).mockImplementation(async () => {
    if (!exists) throw new Error("Einheit wurde gelöscht.");
    return unit as never;
  });
  fetchMock.mockImplementation(async () => { exists = false; return new Response(null, { status: 204 }); });
  const view = show(); await open(); confirm();
  expect(await screen.findByRole("heading", { name: "Testhaus" })).toBeVisible();
  expect(fetchMock.mock.calls[0][0]).toBe("/api/units/unit");
  expect(screen.queryByRole("heading", { name: "Einheit 42" })).not.toBeInTheDocument();
  view.unmount(); const reloaded = show("/objekte/property"); expect(await screen.findByRole("heading", { name: "Testhaus" })).toBeVisible();
  expect(screen.queryByText("Einheit 42")).not.toBeInTheDocument();
  reloaded.unmount(); show();
  expect(await screen.findByText("Error: Einheit wurde gelöscht.")).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Einheit 42" })).not.toBeInTheDocument();
});
it.each([['/objekte/property', 'Objekt löschen', 'Objektübersicht', '/api/properties/property'], ['/personen/person', 'Person löschen', 'Personenübersicht', '/api/persons/person']])("deletes %s and navigates to overview", async (path, label, destination, url) => {
  show(path); await open(label); confirm(); expect(await screen.findByText(destination)).toBeVisible(); expect(fetchMock.mock.calls[0][0]).toBe(url);
});
it("deletes the explicitly identified ended lease and reloads history", async () => {
  leases = [lease]; fetchMock.mockImplementation(async () => { leases = []; return new Response(null, { status: 204 }); });
  show(); const dialog = await open("Mietvertrag löschen"); expect(dialog).toHaveTextContent("lease"); expect(dialog).toHaveTextContent("2025-01-01");
  confirm(); expect(await screen.findByText("Keine Mietvertraege vorhanden.")).toBeVisible(); expect(fetchMock.mock.calls[0][0]).toBe("/api/leases/lease");
});
it.each(['/objekte/property', '/objekte/property/einheiten/unit', '/personen/person'])("hides new deletion controls for READ_ONLY at %s", async path => {
  leases = [lease]; show(path, "READ_ONLY"); await screen.findByRole("heading", { level: 1 });
  expect(screen.queryByRole("button", { name: /löschen/i })).not.toBeInTheDocument();
});
it("hides person deletion when source policy denies preparation", async () => {
  vi.mocked(endpoints.getPerson).mockResolvedValue({ ...person, can_prepare: false } as never); show("/personen/person"); await screen.findByRole("heading", { level: 1 }); expect(screen.queryByRole("button", { name: "Person löschen" })).not.toBeInTheDocument();
});
it("locks duplicate confirmation synchronously while busy", async () => {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementation(() => new Promise<Response>(r => { resolve = r; })); show(); await open(); confirm(); confirm();
  expect(fetchMock).toHaveBeenCalledTimes(1); expect(screen.getByRole("button", { name: "Löschen bestätigen" })).toBeDisabled();
  await act(async () => resolve(new Response(null, { status: 204 }))); expect(await screen.findByRole("heading", { name: "Testhaus" })).toBeVisible();
});
it("ignores a stale delete completion after switching entity routes", async () => {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementation(() => new Promise<Response>(r => { resolve = r; })); show(); await open(); confirm();
  fireEvent.click(screen.getByText("Andere Einheit", { selector: "a" })); expect(await screen.findByRole("heading", { name: "Einheit 99" })).toBeVisible();
  await act(async () => resolve(new Response(null, { status: 204 })));
  expect(screen.getByRole("heading", { name: "Einheit 99" })).toBeVisible(); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("ignores a completion after unmount", async () => {
  let resolve!: (response: Response) => void; fetchMock.mockImplementation(() => new Promise<Response>(r => { resolve = r; }));
  const view = show(); await open(); confirm(); view.unmount(); await act(async () => resolve(new Response(null, { status: 204 }))); expect(fetchMock).toHaveBeenCalledTimes(1);
});
it.each(["READ_ONLY", "CLERK"])("invalidates pending deletion on session change to %s", async role => {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementation(() => new Promise<Response>(r => { resolve = r; }));
  const view = show(); await open(); confirm();
  const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
  view.rerender(content("/objekte/property/einheiten/unit", role, "different-user"));
  expect(signal.aborted).toBe(true);
  await act(async () => resolve(new Response(null, { status: 204 })));
  expect(screen.getByRole("heading", { name: "Einheit 42" })).toBeVisible();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
it("targets only the selected historical lease and preserves the other contract", async () => {
  leases = [lease, { ...lease, id: "lease-other", valid_from: "2024-01-01", valid_to: "2024-12-31" }];
  fetchMock.mockImplementation(async () => { leases = leases.filter(row => row.id !== "lease-other"); return new Response(null, { status: 204 }); });
  show(); const buttons = await screen.findAllByRole("button", { name: "Mietvertrag löschen" }); fireEvent.click(buttons[1]);
  expect(await screen.findByRole("dialog")).toHaveTextContent("lease-other"); confirm();
  expect(await screen.findByText("2025-12-31")).toBeVisible(); expect(screen.queryByText("2024-12-31")).not.toBeInTheDocument();
  expect(fetchMock.mock.calls[0][0]).toBe("/api/leases/lease-other");
});
it("invalidates pending deletion when the same user receives a fresh session", async () => {
  let resolve!: (response: Response) => void;
  fetchMock.mockImplementation(() => new Promise<Response>(r => { resolve = r; }));
  const view = show(); await open(); confirm();
  view.rerender(content());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Einheit löschen" })).toBeEnabled();
  await act(async () => resolve(new Response(null, { status: 204 })));
  expect(screen.getByRole("heading", { name: "Einheit 42" })).toBeVisible();
});
