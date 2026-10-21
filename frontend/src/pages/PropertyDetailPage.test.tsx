import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { PropertyDetailPage } from "./PropertyDetailPage";
import * as endpoints from "../api/endpoints";

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
