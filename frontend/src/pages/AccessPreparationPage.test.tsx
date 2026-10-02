import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AccessPreparationPage } from "./AccessPreparationPage";
import { api } from "../api/client";
import * as auth from "../auth/useAuth";
import type { AccessPreparation } from "../api/types";

vi.mock("../components/RuntimeBudgetPanel", () => ({ RuntimeBudgetPanel: () => null }));
vi.mock("../components/AccessCoveragePanel", () => ({ AccessCoveragePanel: () => null }));

const snapshot: AccessPreparation = {
  generated_at: "2026-10-02T12:00:00Z", mode: "PREPARATION", activation_available: false,
  counts: { properties_total: 5, properties_without_domain: 2, properties_without_current_reader: 3,
    contacts_total: 10, contacts_without_domain: 4, contacts_unknown_creator: 1, contacts_without_current_reader: 3,
    originals_total: 20, bound_originals_without_review_record: 2, originals_quarantined: 1, unbound_originals_unknown_uploader: 1,
    bank_accounts_without_scope: 1, payments_without_scope: 100, cost_entries_without_scope: 4,
    settlements_without_scope: 2, economic_plans_without_scope: 1, dunning_without_context: 3, mandate_originals_without_scope: 2 },
};
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function show(role: "OWNER_ADMIN" | "CLERK" = "OWNER_ADMIN") {
  vi.spyOn(auth, "useAuth").mockReturnValue({ loading: false, session: { user_id: "actor", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() });
  render(<MemoryRouter><AccessPreparationPage /></MemoryRouter>);
}

it("shows aggregate gaps and explicit setup links without an activation action", async () => {
  vi.spyOn(api, "get").mockResolvedValue(snapshot);
  show();
  expect(await screen.findByText("100")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Aktivierung noch nicht verfügbar" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Altquellen prüfen" })).toHaveAttribute("href", "/altquellen");
  expect(screen.queryByRole("button", { name: /aktivieren/i })).not.toBeInTheDocument();
});

it("hides stale counts after failed refresh and supports retry", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValueOnce(snapshot).mockRejectedValueOnce(new Error("Bestand nicht verfügbar")).mockResolvedValueOnce(snapshot);
  show();
  await screen.findByText("100");
  fireEvent.click(screen.getByRole("button", { name: "Bestand aktualisieren" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Bestand nicht verfügbar");
  expect(screen.queryByText("100")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Bestand aktualisieren" }));
  await screen.findByText("100");
  await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
});

it("does not request administrative counts for a clerk", () => {
  const get = vi.spyOn(api, "get");
  show("CLERK");
  expect(screen.getByText("Zugriff nur für Inhaber/Admin.")).toBeVisible();
  expect(get).not.toHaveBeenCalled();
});
