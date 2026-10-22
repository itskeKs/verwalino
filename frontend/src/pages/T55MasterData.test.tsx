import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { MasterDataEditor } from "../components/MasterDataEditor";
import { BankAccountsPage } from "./BankAccountsPage";
import { PropertiesPage } from "./PropertiesPage";
import { PropertyDetailPage } from "./PropertyDetailPage";
import { PersonDetailPage } from "./PersonDetailPage";
import { MandateDetailPage } from "./MandateDetailPage";

vi.mock("../components/AttachmentsPanel", () => ({ AttachmentsPanel: () => null }));
vi.mock("../components/MeterPanel", () => ({ MeterPanel: () => null }));
vi.mock("../components/DsgvoPanel", () => ({ DsgvoPanel: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const bank = { id: "bank", property_id: "house", property_name: "Haus", account_type: "LAUFEND", label: "Altes Konto", iban: "DE123", bic: null, bank_name: "Bank" };
const house = { id: "house", name: "Haus", street: "", zip_code: "", city: "", management_type: "MIET", fiscal_year_start_month: 1, construction_year: 1901, total_area: "123.45" };
const vendor = { id: "vendor", person_id: "person", tax_id: "123", vat_id: null, notes: "Alt" };
const mandate = { id: "mandate", label: "Verwaltung", mandate_type: "MIET", start_date: "2025-01-01", end_date: null };
function mount(path: string, component: React.ReactNode, role = "OWNER_ADMIN", route = path) {
  return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={[path]}><Routes><Route path={route} element={component} /></Routes></MemoryRouter></AuthCtx.Provider>);
}
function setup(options: { failure?: number; canPrepare?: boolean; unknownBuilding?: boolean } = {}) {
  let currentBank = { ...bank, is_active: true };
  let currentHouse = { ...house, ...(options.unknownBuilding ? { construction_year: null, total_area: null } : {}) };
  let currentVendor = { ...vendor, is_active: true };
  let currentMandate = { ...mandate };
  const writes: { url: string; method: string; body: Record<string, unknown> }[] = [];
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") {
      const body = JSON.parse(String(init.body));
      writes.push({ url, method: init.method, body });
      if (options.failure) { const status = options.failure; options.failure = undefined; return json({ detail: status === 409 ? "Identität bereits verwendet" : "Keine Berechtigung" }, status); }
      if (url === "/api/bank-accounts/bank") { currentBank = { ...currentBank, ...body }; return json(currentBank); }
      if (url === "/api/properties/house") { currentHouse = { ...currentHouse, ...body }; return json(currentHouse); }
      if (url === "/api/vendors/vendor") { currentVendor = { ...currentVendor, ...body }; return json(currentVendor); }
      if (url === "/api/mandates/mandate") { currentMandate = { ...currentMandate, ...body }; return json(currentMandate); }
      if (url === "/api/properties") return json({ ...currentHouse, ...body }, 201);
      throw new Error(`Unexpected write ${url}`);
    }
    if (url.startsWith("/api/bank-accounts")) return json([currentBank]);
    if (url.startsWith("/api/properties/page")) return json({ items: [currentHouse], next_cursor: null });
    if (url === "/api/properties/house") return json(currentHouse);
    if (url.startsWith("/api/properties/house/knowledge")) return json({ documents: [], rules_by_topic: {} });
    if (url.startsWith("/api/units")) return json([]);
    if (url === "/api/persons/person" || url === "/api/people/person") return json({ id: "person", first_name: "Ada", last_name: "Test", can_prepare: options.canPrepare !== false });
    if (url.startsWith("/api/owner-profiles") || url.startsWith("/api/tenant-profiles")) return json([]);
    if (url.startsWith("/api/vendors")) return json([currentVendor]);
    if (url === "/api/mandates/mandate") return json(currentMandate);
    if (url.startsWith("/api/mandates/mandate/documents")) return json([]);
    throw new Error(`Unexpected read ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return { writes, fetcher };
}
it("bank rename persists by PATCH; cancel writes nothing and history stays visible after confirmed end/reactivation", async () => {
  const { writes } = setup();
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  mount("/bankkonten", <BankAccountsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Bankkonto bearbeiten" }));
  let dialog = screen.getByRole("dialog", { name: "Bankkonto bearbeiten" });
  fireEvent.change(within(dialog).getByLabelText("Bezeichnung"), { target: { value: "Verworfen" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
  expect(writes).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Bankkonto bearbeiten" }));
  dialog = screen.getByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("Bezeichnung"), { target: { value: "Neues Konto" } });
  fireEvent.click(within(dialog).getByRole("button", { name: "Änderungen speichern" }));
  expect(await screen.findByText("Neues Konto")).toBeVisible();
  expect(writes[0]).toEqual({ url: "/api/bank-accounts/bank", method: "PATCH", body: { label: "Neues Konto" } });
  fireEvent.click(screen.getByRole("button", { name: "Bankkonto beenden" }));
  expect(confirm).toHaveBeenCalled(); expect(writes).toHaveLength(1);
  confirm.mockReturnValue(true);
  fireEvent.click(screen.getByRole("button", { name: "Bankkonto beenden" }));
  expect(await screen.findByText("Beendet")).toBeVisible();
  expect(screen.getByRole("link", { name: "Umsätze" })).toBeVisible();
  expect(writes[1].body).toEqual({ is_active: false });
  fireEvent.click(screen.getByRole("button", { name: "Bankkonto reaktivieren" }));
  await screen.findByRole("button", { name: "Bankkonto beenden" });
  expect(writes[2].body).toEqual({ is_active: true });
});
it.each([403, 409])("bank editor retains input on %s and permits retry", async failure => {
  const { writes } = setup({ failure });
  mount("/bankkonten", <BankAccountsPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Bankkonto bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Bezeichnung"), { target: { value: "Neu" } });
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(failure === 409 ? "Identität bereits verwendet" : "Keine Berechtigung");
  expect(screen.getByLabelText("Bezeichnung")).toHaveValue("Neu");
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  await screen.findByText("Neu"); expect(writes).toHaveLength(2);
});
it("read-only bank controls are hidden", async () => {
  setup(); mount("/bankkonten", <BankAccountsPage />, "READ_ONLY");
  await screen.findByText("Altes Konto");
  expect(screen.queryByRole("button", { name: /Neues Bankkonto|Bankkonto bearbeiten|Bankkonto beenden/ })).not.toBeInTheDocument();
});
it("vendor edits the profile, not its linked person, and confirms ending", async () => {
  const { writes } = setup(); vi.spyOn(window, "confirm").mockReturnValue(true);
  mount("/personen/person", <PersonDetailPage />, "CLERK", "/personen/:personId");
  fireEvent.click(await screen.findByRole("button", { name: "Dienstleister bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Steuer-Nr."), { target: { value: "456" } });
  fireEvent.change(screen.getByLabelText("Notizen"), { target: { value: "Neu" } });
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  await screen.findByText("456");
  expect(writes[0]).toEqual({ url: "/api/vendors/vendor", method: "PUT", body: { tax_id: "456", notes: "Neu" } });
  fireEvent.click(screen.getByRole("button", { name: "Dienstleister beenden" }));
  await screen.findByText("Beendet"); expect(writes[1].body).toEqual({ is_active: false });
});
it.each([["READ_ONLY", true], ["CLERK", false]])("vendor editor respects role %s and can_prepare %s", async (role, canPrepare) => {
  setup({ canPrepare }); mount("/personen/person", <PersonDetailPage />, role as string, "/personen/:personId");
  await screen.findByText("Dienstleister-Details");
  expect(screen.queryByRole("button", { name: /Dienstleister bearbeiten|Dienstleister beenden/ })).not.toBeInTheDocument();
});
it("admin mandate editor PATCHes label only and explicitly confirms an end date", async () => {
  const { writes } = setup(); const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  mount("/mandate/mandate", <MandateDetailPage />, "OWNER_ADMIN", "/mandate/:mandateId");
  fireEvent.click(await screen.findByRole("button", { name: "Mandat bearbeiten" }));
  fireEvent.change(within(screen.getByRole("dialog")).getByLabelText("Bezeichnung"), { target: { value: "Neu" } });
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  await screen.findByRole("heading", { name: "Neu" });
  expect(writes[0]).toEqual({ url: "/api/mandates/mandate", method: "PATCH", body: { label: "Neu" } });
  fireEvent.click(screen.getByRole("button", { name: "Mandat bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Ende"), { target: { value: "2026-12-31" } });
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  await waitFor(() => expect(writes).toHaveLength(2)); expect(confirm).toHaveBeenCalled();
  expect(writes[1].body).toEqual({ end_date: "2026-12-31" });
});
it.each(["CLERK", "READ_ONLY"])("mandate editing is admin only (%s)", async role => {
  setup(); mount("/mandate/mandate", <MandateDetailPage />, role, "/mandate/:mandateId");
  await screen.findByRole("heading", { name: "Verwaltung" });
  expect(screen.queryByRole("button", { name: "Mandat bearbeiten" })).not.toBeInTheDocument();
});
it("property editing keeps decimal area as a string and clears both optional fields with null", async () => {
  const { writes } = setup(); mount("/objekte", <PropertiesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Bearbeiten" }));
  expect(screen.getByLabelText("Baujahr")).toHaveValue(1901);
  fireEvent.change(screen.getByLabelText("Gesamtfläche (m²)"), { target: { value: "999999999999.99" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].body.total_area).toBe("999999999999.99");
  await waitFor(() => expect(screen.queryByLabelText("Baujahr")).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Baujahr"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Gesamtfläche (m²)"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(writes[1].body).toMatchObject({ construction_year: null, total_area: null });
});
it("property creation offers optional building fields", async () => {
  const { writes } = setup(); mount("/objekte", <PropertiesPage />);
  fireEvent.click(screen.getByRole("button", { name: "+ Neues Objekt" }));
  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Neu" } });
  fireEvent.change(screen.getByLabelText("Baujahr"), { target: { value: "2020" } });
  fireEvent.change(screen.getByLabelText("Gesamtfläche (m²)"), { target: { value: "100.50" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0]).toMatchObject({ url: "/api/properties", method: "POST", body: { construction_year: 2020, total_area: "100.50" } });
});
it("property details display stored building data rather than a unit-derived sum", async () => {
  setup(); mount("/objekte/house", <PropertyDetailPage />, "READ_ONLY", "/objekte/:propertyId");
  expect(await screen.findByText("Baujahr")).toBeVisible();
  expect(screen.getByText("1901")).toBeVisible();
  expect(screen.getByText("Gesamtfläche (m²)")).toBeVisible();
  expect(screen.getByText("123.45")).toBeVisible();
});

it.each([403, 409])("vendor and mandate editors preserve server error %s and allow retry", async failure => {
  const { writes } = setup({ failure });
  const view = mount("/personen/person", <PersonDetailPage />, "CLERK", "/personen/:personId");
  fireEvent.click(await screen.findByRole("button", { name: "Dienstleister bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Steuer-Nr."), { target: { value: "999" } });
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(failure === 409 ? "Identität bereits verwendet" : "Keine Berechtigung");
  expect(screen.getByLabelText("Steuer-Nr.")).toHaveValue("999");
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  await screen.findByText("999"); expect(writes).toHaveLength(2);
  view.unmount();
  const next = setup({ failure });
  mount("/mandate/mandate", <MandateDetailPage />, "OWNER_ADMIN", "/mandate/:mandateId");
  fireEvent.click(await screen.findByRole("button", { name: "Mandat bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Verwaltungsart"), { target: { value: "WEG" } });
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(failure === 409 ? "Identität bereits verwendet" : "Keine Berechtigung");
  expect(screen.getByLabelText("Verwaltungsart")).toHaveValue("WEG");
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(next.writes[1]).toMatchObject({ method: "PATCH", body: { mandate_type: "WEG" } });
});
it("cancelled vendor and mandate forms do not persist changes", async () => {
  const { writes } = setup();
  const view = mount("/personen/person", <PersonDetailPage />, "CLERK", "/personen/:personId");
  fireEvent.click(await screen.findByRole("button", { name: "Dienstleister bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Notizen"), { target: { value: "Verwerfen" } });
  fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  expect(writes).toHaveLength(0); expect(screen.getByText("Alt")).toBeVisible();
  view.unmount();
  const next = setup();
  mount("/mandate/mandate", <MandateDetailPage />, "OWNER_ADMIN", "/mandate/:mandateId");
  fireEvent.click(await screen.findByRole("button", { name: "Mandat bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Ende"), { target: { value: "2026-12-31" } });
  vi.spyOn(window, "confirm").mockReturnValue(false);
  fireEvent.click(screen.getByRole("button", { name: "Änderungen speichern" }));
  expect(next.writes).toHaveLength(0); expect(screen.getByRole("dialog")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  expect(screen.getByText("laufend")).toBeVisible();
});
it("unchanged optional object fields are omitted and invalid decimal strings never reach the API", async () => {
  const { writes } = setup(); mount("/objekte", <PropertiesPage />);
  fireEvent.click(await screen.findByRole("button", { name: "Bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Name *"), { target: { value: "Umbenannt" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].body).not.toHaveProperty("construction_year");
  expect(writes[0].body).not.toHaveProperty("total_area");
  await waitFor(() => expect(screen.queryByLabelText("Baujahr")).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  for (const invalid of ["1e3", "-1", "1.234", "NaN", "1000000000000.00"]) {
    fireEvent.change(screen.getByLabelText("Gesamtfläche (m²)"), { target: { value: invalid } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    expect(screen.getByText(/Gesamtfläche muss eine/)).toBeVisible();
    expect(writes).toHaveLength(1);
  }
  fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  expect(writes).toHaveLength(1);
});
it("unknown building data displays dashes, not guessed values", async () => {
  setup({ unknownBuilding: true }); mount("/objekte/house", <PropertyDetailPage />, "READ_ONLY", "/objekte/:propertyId");
  const year = await screen.findByText("Baujahr");
  expect(year.closest("tr")).toHaveTextContent("—");
  expect(screen.getByText("Gesamtfläche (m²)").closest("tr")).toHaveTextContent("—");
});
it("editor blocks duplicate writes and ignores a completion after its route generation unmounts", async () => {
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  const save = vi.fn(() => pending);
  const onSaved = vi.fn();
  const view = render(<MasterDataEditor key="old" title="Bearbeiten" initial={{ label: "Alt" }} fields={[{ key: "label", label: "Bezeichnung" }]} save={save} onSaved={onSaved} />);
  fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Bezeichnung"), { target: { value: "Neu" } });
  const form = screen.getByLabelText("Bezeichnung").closest("form")!;
  fireEvent.submit(form); fireEvent.submit(form);
  expect(save).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  view.rerender(<MasterDataEditor key="new" title="Anderes Objekt" initial={{ label: "Anders" }} fields={[{ key: "label", label: "Bezeichnung" }]} save={save} onSaved={onSaved} />);
  await act(async () => { finish(); await pending; });
  expect(onSaved).not.toHaveBeenCalled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Anderes Objekt" })).toBeEnabled();
});
