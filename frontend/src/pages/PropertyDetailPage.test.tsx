import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { PropertyDetailPage } from "./PropertyDetailPage";
import * as endpoints from "../api/endpoints";

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
