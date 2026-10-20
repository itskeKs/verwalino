import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { UnitDetailPage } from "./UnitDetailPage";
import * as endpoints from "../api/endpoints";

vi.mock("../api/endpoints", () => ({
  getUnit: vi.fn(), getProperty: vi.fn(), listOwnership: vi.fn(), listLeases: vi.fn(),
  listOwnerProfiles: vi.fn(), listTenantProfiles: vi.fn(), listPersons: vi.fn(),
  createOwnership: vi.fn(), createLease: vi.fn(), patchLease: vi.fn(),
}));

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(endpoints.getUnit).mockResolvedValue({ id: "unit", number: "1" } as never);
  vi.mocked(endpoints.getProperty).mockResolvedValue({ id: "property", name: "Testhaus" } as never);
  vi.mocked(endpoints.listOwnership).mockResolvedValue([]);
  vi.mocked(endpoints.listLeases).mockResolvedValue([{
    id: "lease", unit_id: "unit", tenant_profile_id: "tenant", valid_from: "2025-01-01",
    valid_to: null, base_rent: "500.00", advance_payment: "100.00", deposit: null, notes: "Notiz",
  }]);
  vi.mocked(endpoints.listOwnerProfiles).mockResolvedValue([]);
  vi.mocked(endpoints.listTenantProfiles).mockResolvedValue([]);
  vi.mocked(endpoints.listPersons).mockResolvedValue([]);
  vi.mocked(endpoints.patchLease).mockResolvedValue({} as never);
});

it("ends an existing lease without resending unchanged monetary fields", async () => {
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={["/objekte/property/einheiten/unit"]}>
    <Routes><Route path="/objekte/:propertyId/einheiten/:unitId" element={<UnitDetailPage />} /></Routes>
  </MemoryRouter>);
  await user.click(await screen.findByRole("button", { name: "Bearbeiten / beenden" }));
  expect(screen.getByRole("combobox", { name: /^Mieter \*$/ })).toHaveAccessibleName("Mieter *");
  expect(screen.getByLabelText("Vertragsbeginn *")).toBeDisabled();
  await user.type(screen.getByLabelText("Vertragsende"), "2025-12-31");
  await user.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(endpoints.patchLease).toHaveBeenCalledWith("lease", {
    valid_to: "2025-12-31", notes: "Notiz",
  }));
  expect(endpoints.createLease).not.toHaveBeenCalled();
});
