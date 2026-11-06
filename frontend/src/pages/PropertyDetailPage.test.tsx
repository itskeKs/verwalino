import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { PropertyDetailPage } from "./PropertyDetailPage";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import type { ManagementType, MeaScaleStatus, Unit } from "../api/types";

vi.mock("../components/AttachmentsPanel", () => ({
  AttachmentsPanel: ({ propertyId, unitId, personId, canWrite }: { propertyId?: string; unitId?: string; personId?: string; canWrite: boolean }) =>
    <section aria-label="Dokumente" data-property={propertyId} data-unit={unitId} data-person={personId} data-can-write={String(canWrite)} />,
}));

vi.mock("../components/MeterPanel", () => ({
  MeterPanel: ({ propertyId, unitId, canWrite }: { propertyId: string; unitId?: string; canWrite: boolean }) =>
    <section aria-label="Zähler" data-property={propertyId} data-unit={unitId} data-can-write={String(canWrite)} />,
}));
vi.mock("../api/endpoints", async (importOriginal) => ({
  ...await importOriginal<typeof endpoints>(),
  getProperty: vi.fn(), listUnits: vi.fn(), getPropertyKnowledge: vi.fn(),
}));

afterEach(cleanup);
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(endpoints.getProperty).mockResolvedValue({
    id: "property", name: "T30 Haus", management_type: "MIET", fiscal_year_start_month: 1,
    mea_scale_status: "CONFIRMED", mea_denominator: 1000,
  } as never);
  vi.mocked(endpoints.listUnits).mockResolvedValue([]);
  vi.mocked(endpoints.getPropertyKnowledge).mockResolvedValue(null as never);
});

function renderMeaProperty(managementType: ManagementType, mea: (string | null)[], status: MeaScaleStatus = "LEGACY_UNCONFIRMED", role = "CLERK") {
  vi.mocked(endpoints.getProperty).mockResolvedValue({
    id: "property", name: "Musterhaus", management_type: managementType,
    fiscal_year_start_month: 1, mea_scale_status: status,
    mea_denominator: status === "CONFIRMED" ? 1000 : null,
  } as never);
  vi.mocked(endpoints.listUnits).mockResolvedValue(mea.map((share, index): Unit => ({
    id: `unit-${index}`, property_id: "property", number: String(index + 1),
    location: "Gartenflügel", usage_type: "WOHNUNG", area: null, heated_area: null,
    mea: share, building_part: "",
  })));
  return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={["/objekte/property"]}><Routes><Route path="/objekte/:propertyId" element={<PropertyDetailPage />} /></Routes></MemoryRouter>
  </AuthCtx.Provider>);
}

it.each([{ shares: [] }, { shares: [null, null] }])("hides MEA confirmation and the unit field for MIET without recorded shares ($shares)", async ({ shares }) => {
  renderMeaProperty("MIET", shares);
  await screen.findByRole("heading", { name: "Musterhaus" });
  expect(screen.queryByRole("heading", { name: "MEA-Skala bestätigen" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Einheit" }));
  expect(screen.queryByLabelText("MEA (Miteigentumsanteil)")).not.toBeInTheDocument();
});

it.each<ManagementType>(["WEG", "MISCH", "SEV"])("shows German MEA confirmation and the unit field for %s without shares", async managementType => {
  renderMeaProperty(managementType, [null]);
  expect(await screen.findByRole("heading", { name: "MEA-Skala bestätigen" })).toBeVisible();
  expect(screen.getByText("Skala noch nicht bestätigt")).toBeVisible();
  expect(screen.queryByText("LEGACY_UNCONFIRMED")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Einheit" }));
  expect(screen.getByLabelText("MEA (Miteigentumsanteil)")).toBeVisible();
});

it.each(["250.000000", "0.000000"])("keeps MEA confirmation and existing MIET share %s editable", async share => {
  renderMeaProperty("MIET", [null, share]);
  expect(await screen.findByRole("heading", { name: "MEA-Skala bestätigen" })).toBeVisible();
  expect(screen.getByText(`${share} (Zähler/Skala unbestätigt)`)).toBeVisible();
  fireEvent.click(screen.getAllByRole("button", { name: "Bearbeiten" })[1]);
  expect(screen.getByLabelText("MEA (Miteigentumsanteil)")).toHaveValue(share);
});

it("keeps confirmed MIET shares editable without asking to confirm again", async () => {
  renderMeaProperty("MIET", ["250.000000"], "CONFIRMED");
  await screen.findByRole("heading", { name: "Musterhaus" });
  expect(screen.queryByRole("heading", { name: "MEA-Skala bestätigen" })).not.toBeInTheDocument();
  expect(screen.getByText("250.000000 / 1.000")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  expect(screen.getByLabelText("MEA (Miteigentumsanteil)")).toHaveValue("250.000000");
});

it("confirms the WEG scale through the existing action and refreshes the prompt", async () => {
  renderMeaProperty("WEG", []);
  vi.spyOn(endpoints, "confirmMeaScale").mockResolvedValue({} as never);
  await screen.findByRole("heading", { name: "MEA-Skala bestätigen" });
  vi.mocked(endpoints.getProperty).mockResolvedValue({
    id: "property", name: "Musterhaus", management_type: "WEG", fiscal_year_start_month: 1,
    mea_scale_status: "CONFIRMED", mea_denominator: 1000,
  } as never);
  fireEvent.change(screen.getByLabelText("Nenner *"), { target: { value: "1000" } });
  fireEvent.click(screen.getByRole("button", { name: "MEA-Skala bestätigen" }));
  expect(await screen.findByRole("status")).toHaveTextContent("MEA-Skala gespeichert.");
  expect(endpoints.confirmMeaScale).toHaveBeenCalledWith("property", 1000);
  expect(screen.queryByRole("heading", { name: "MEA-Skala bestätigen" })).not.toBeInTheDocument();
});

it("keeps MEA confirmation and unit editing unavailable to read-only users", async () => {
  renderMeaProperty("WEG", ["250.000000"], "LEGACY_UNCONFIRMED", "READ_ONLY");
  await screen.findByRole("heading", { name: "Musterhaus" });
  expect(screen.queryByRole("heading", { name: "MEA-Skala bestätigen" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "+ Neue Einheit" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Bearbeiten" })).not.toBeInTheDocument();
});

it("keeps the property and panels while a saved unit refreshes", async () => {
  let finish!: (value: never) => void;
  vi.spyOn(endpoints, "createUnit").mockResolvedValue({ id: "new-unit" } as never);
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role: "CLERK" }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/objekte/property"]}><Routes><Route path="/objekte/:propertyId" element={<PropertyDetailPage />} /></Routes></MemoryRouter></AuthCtx.Provider>);
  const panel = await screen.findByRole("region", { name: "Zähler" });
  vi.mocked(endpoints.getProperty).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  vi.mocked(endpoints.listUnits).mockResolvedValue([{ id: "new-unit", number: "42", location: "Gartenflügel", usage_type: "WOHNUNG" }] as never);
  fireEvent.click(screen.getByRole("button", { name: "+ Neue Einheit" }));
  fireEvent.change(screen.getByLabelText("Nummer *"), { target: { value: "42" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Einheit gespeichert.");
  expect(screen.getByRole("heading", { name: "T30 Haus" })).toBeVisible();
  expect(panel).toBeInTheDocument();
  await act(async () => finish({ id: "property", name: "T30 Haus", management_type: "MIET", fiscal_year_start_month: 1 } as never));
  expect(await screen.findByRole("link", { name: "42" })).toBeVisible();
});

it("shows a rejected unit number at the field and saves after shortening it", async () => {
  renderMeaProperty("MIET", [null]);
  const message = "Die Bezeichnung darf höchstens 50 Zeichen lang sein.";
  const validNumber = "Ä".repeat(50);
  const tooLong = validNumber + "X";
  const update = vi.spyOn(endpoints, "updateUnit")
    .mockRejectedValueOnce(new ApiError(422, message, "unit_number_too_long"))
    .mockResolvedValueOnce({ number: validNumber } as Unit);
  fireEvent.click(await screen.findByRole("button", { name: "Bearbeiten" }));
  const input = screen.getByRole("textbox", { name: "Nummer *" });
  fireEvent.change(input, { target: { value: tooLong } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByText(message)).toBeVisible();
  expect(input).toHaveAccessibleDescription(message);
  expect(input).toHaveAttribute("aria-invalid", "true");
  expect(input).toHaveValue(tooLong);
  expect(update).toHaveBeenNthCalledWith(1, "unit-0", expect.objectContaining({ number: tooLong }));

  fireEvent.change(input, { target: { value: validNumber } });
  expect(screen.queryByText(message)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Einheit gespeichert.");
  expect(update).toHaveBeenNthCalledWith(2, "unit-0", expect.objectContaining({ number: validNumber }));
  expect(screen.queryByRole("heading", { name: "Einheit bearbeiten" })).not.toBeInTheDocument();
});

it.each([["OWNER_ADMIN", true], ["CLERK", true], ["READ_ONLY", false], ["UNKNOWN", false]] as const)(
  "binds the property meter panel without a unit and with role %s", async (role, canWrite) => {
    render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}>
      <MemoryRouter initialEntries={["/objekte/property"]}>
        <Routes><Route path="/objekte/:propertyId" element={<PropertyDetailPage />} /></Routes>
      </MemoryRouter>
    </AuthCtx.Provider>);
    const panel = await screen.findByRole("region", { name: "Zähler" });
    expect(panel).toHaveAttribute("data-property", "property");
    expect(panel).not.toHaveAttribute("data-unit");
    expect(panel).toHaveAttribute("data-can-write", String(canWrite));
  },
);

it.each([["OWNER_ADMIN", true], ["CLERK", true], ["READ_ONLY", false], ["UNKNOWN", false]] as const)("T37 binds loaded property documents for %s", async (role, canWrite) => {
  vi.mocked(endpoints.getProperty).mockResolvedValue({ id: "loaded-property", name: "Loaded", management_type: "MIET", fiscal_year_start_month: 1 } as never);
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() }}>
    <MemoryRouter initialEntries={["/objekte/property"]}><Routes><Route path="/objekte/:propertyId" element={<PropertyDetailPage />} /></Routes></MemoryRouter>
  </AuthCtx.Provider>);
  const panel = await screen.findByRole("region", { name: "Dokumente" });
  expect(panel).toHaveAttribute("data-property", "loaded-property"); expect(panel).not.toHaveAttribute("data-unit"); expect(panel).toHaveAttribute("data-can-write", String(canWrite));
});

it("T37 ignores a late property response after changing the route", async () => {
  let resolveOld!: (value: Awaited<ReturnType<typeof endpoints.getProperty>>) => void;
  vi.mocked(endpoints.getProperty).mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve; })).mockResolvedValue({ id: "new-property", name: "Neues Haus", management_type: "MIET", fiscal_year_start_month: 1 } as never);
  render(<AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/objekte/property"]}>
    <Link to="/objekte/new-property">Anderes Objekt</Link><Routes><Route path="/objekte/:propertyId" element={<PropertyDetailPage />} /></Routes>
  </MemoryRouter></AuthCtx.Provider>);
  fireEvent.click(screen.getByRole("link", { name: "Anderes Objekt" }));
  await screen.findByRole("heading", { name: "Neues Haus" });
  await act(async () => resolveOld({ id: "property", name: "Altes Haus", management_type: "MIET", fiscal_year_start_month: 1 } as never));
  expect(screen.getByRole("region", { name: "Dokumente" })).toHaveAttribute("data-property", "new-property");
  expect(screen.queryByRole("heading", { name: "Altes Haus" })).not.toBeInTheDocument();
});

it.each(["CLERK", "OWNER_ADMIN", "READ_ONLY", "UNKNOWN"])("T42 offers context task creation only to writers (%s), with loaded IDs", async role => {
  vi.mocked(endpoints.getProperty).mockResolvedValue({ id: "loaded/p&1", name: "Loaded", management_type: "MIET", fiscal_year_start_month: 1 } as never);
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/objekte/route-id"]}><Routes><Route path="/objekte/:propertyId" element={<PropertyDetailPage />} /></Routes></MemoryRouter></AuthCtx.Provider>);
  await screen.findByRole("heading", { name: "Loaded" });
  const link = screen.queryByRole("link", { name: "Aufgabe anlegen" });
  if (role === "CLERK" || role === "OWNER_ADMIN") {
    expect(link).toHaveAttribute("href", "/aufgaben?property_id=loaded%2Fp%261&create=1");
  } else expect(link).toBeNull();
});
