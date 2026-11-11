import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import { AuthCtx } from "../auth/useAuth";
import { PermissionsOverview } from "./PermissionsPage";

const inventory = {
  generated_at: "2026-11-01T10:00:00Z",
  mode: "PREPARATION",
  activation_available: false,
  counts: {
    properties_total: 3, properties_without_domain: 1, properties_without_current_reader: 0,
    contacts_total: 12, contacts_without_domain: 2, contacts_unknown_creator: 0, contacts_without_current_reader: 4,
    originals_total: 7, bound_originals_without_review_record: 1, originals_quarantined: 2, unbound_originals_unknown_uploader: 0,
    bank_accounts_without_scope: 0, payments_without_scope: 0, cost_entries_without_scope: 0,
    settlements_without_scope: 0, economic_plans_without_scope: 0, dunning_without_context: 3,
    mandate_originals_without_scope: 0,
  },
};

function renderPage(role: string) {
  return render(
    <AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() } as never}>
      <MemoryRouter>
        <PermissionsOverview />
      </MemoryRouter>
    </AuthCtx.Provider>,
  );
}

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the source security status and the inventory counts to an owner admin", async () => {
  const get = vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/access-preparation/source-security") return { mode: "PREPARATION", changed_at: null } as never;
    if (path === "/api/access-preparation") return inventory as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  renderPage("OWNER_ADMIN");

  expect(await screen.findByText("Vorbereitung")).toBeVisible();
  expect(screen.getByText(/Der objektbezogene Quellschutz ist noch aus/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Zum nächsten Schritt" })).toHaveAttribute("href", "/berechtigungen/bestand");
  expect(screen.getByText("Objekte ohne Datenbereich")).toBeVisible();
  expect(screen.getByText("Mahnungen ohne geprüften Ursprung")).toBeVisible();
  expect(screen.getByText("4")).toBeVisible();
  expect(get).toHaveBeenCalledWith("/api/access-preparation/source-security");
  expect(get).toHaveBeenCalledWith("/api/access-preparation");
});

it("reports an unavailable status and denies access to a clerk", async () => {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/access-preparation") return inventory as never;
    throw new Error("offline");
  });
  renderPage("OWNER_ADMIN");
  expect(await screen.findByRole("alert")).toHaveTextContent("Der aktuelle Quellschutz ist nicht verfügbar. Bitte erneut laden.");
  expect(screen.getByText("Objekte ohne Datenbereich")).toBeVisible();

  cleanup();
  renderPage("CLERK");
  expect(await screen.findByText("Zugriff nur für Inhaber/Admin.")).toBeVisible();
});
