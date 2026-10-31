import { api } from "./api/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { AppRoutes } from "./App";
import * as endpoints from "./api/endpoints";
import * as letters from "./api/letters";

let permissionsMode = "PREPARATION";
let permissionsStatusFails = false;
let permissionsInventoryFails = false;
function CurrentPath() { return <output aria-label="Aktuelle Route">{useLocation().pathname}</output>; }
const inventory = { generated_at: "2026-10-31T12:00:00Z", mode: "PREPARATION", counts: {
  properties_total: 5, properties_without_domain: 2, properties_without_current_reader: 3,
  contacts_total: 10, contacts_without_domain: 4, contacts_unknown_creator: 1, contacts_without_current_reader: 3,
  originals_total: 20, bound_originals_without_review_record: 2, originals_quarantined: 1, unbound_originals_unknown_uploader: 1,
  bank_accounts_without_scope: 1, payments_without_scope: 100, cost_entries_without_scope: 4,
  settlements_without_scope: 2, economic_plans_without_scope: 1, dunning_without_context: 3, mandate_originals_without_scope: 2,
} };

function renderRoutes(path: string, role?: string, setupStatus: { required: boolean } | Error = { required: false }) {
  if (role) {
    vi.spyOn(endpoints, "getCurrentSession").mockResolvedValue({
      user_id: "user-1",
      organization_id: "org-1",
      role,
    });
  } else {
    vi.spyOn(endpoints, "getCurrentSession").mockRejectedValue(new Error("not authenticated"));
  }
  vi.spyOn(endpoints, "getMyOrganization").mockResolvedValue({ id: "org-1", name: "Test" });
  vi.spyOn(api, "get").mockImplementation((url) => {
    if (url === "/api/setup") {
      return setupStatus instanceof Error ? Promise.reject(setupStatus) : Promise.resolve(setupStatus);
    }
    if (url === "/api/access-preparation") return permissionsInventoryFails ? Promise.reject(new Error("unavailable")) : Promise.resolve(inventory);
    if (url === "/api/access-preparation/source-security") return permissionsStatusFails ? Promise.reject(new Error("unavailable")) : Promise.resolve({ mode: permissionsMode, changed_at: null, last_transition: null });
    if (url === "/api/access-preparation/runtime-budget") return Promise.resolve({ policy: { enabled: false, max_cost_usd: "1.00", window_minutes: 60, reservation_seconds: 120 }, policy_digest: "a".repeat(64), review: null, matches_reviewed_policy: false });
    if (url === "/api/accounting-scopes/control") return Promise.resolve({ mode: "compatibility", event: null });
    return Promise.resolve({ items: [], next_cursor: null });
  });
  vi.spyOn(endpoints, "getPropertyKnowledge").mockResolvedValue({ property_id: "property-1", documents: [], document_relations: [], rules_by_topic: {}, open_rule_topics: [] });
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><AppRoutes /><CurrentPath /></AuthProvider></MemoryRouter>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  permissionsMode = "PREPARATION";
  permissionsStatusFails = false;
  permissionsInventoryFails = false;
});

describe("setup status availability", () => {
  it.each([undefined, "OWNER_ADMIN"])("keeps normal routes available after a failed setup check for %s", async role => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    renderRoutes("/objekte", role, new TypeError("network"));
    expect(await screen.findByRole("heading", { name: role ? "Objekte" : "Hausverwaltung" })).toBeVisible();
    expect(screen.queryByText(/Einrichtungsstatus konnte nicht geladen werden/)).toBeNull();
    expect(log).toHaveBeenCalled();
  });

  it("opens setup when a later app load can check a fresh installation", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const firstLoad = renderRoutes("/", undefined, new TypeError("network"));
    expect(await screen.findByRole("heading", { name: "Hausverwaltung" })).toBeVisible();
    firstLoad.unmount();
    renderRoutes("/", undefined, { required: true });
    expect(await screen.findByRole("heading", { name: "Verwalino einrichten" })).toBeVisible();
  });
});

describe("declaration route authorization", () => {
  it("redirects READ_ONLY from draft imports", async () => {
    renderRoutes("/teilungserklaerung/import/import-1/review", "READ_ONLY");
    expect(await screen.findByRole("heading", { name: "Objekte" })).toBeVisible();
    expect(screen.queryByText("Teilungserklärung importieren")).toBeNull();
  });

  it("keeps unauthenticated visitors on the login page", async () => {
    renderRoutes("/teilungserklaerung/import/import-1/review");
    expect(await screen.findByRole("heading", { name: "Hausverwaltung" })).toBeVisible();
  });

  it("keeps property knowledge reachable for read-only users", async () => {
    renderRoutes("/objekte/property-1/wissen", "READ_ONLY");
    expect(await screen.findByRole("heading", { name: "Dokumente & Wissen" })).toBeVisible();
  });
});


describe("deposit navigation", () => {
  it.each(["READ_ONLY", "CLERK"])("opens deposits via its sidebar route for %s", async role => {
    vi.spyOn(endpoints, "listLeases").mockResolvedValue([]);
    vi.spyOn(endpoints, "listDeposits").mockResolvedValue([]);
    vi.spyOn(endpoints, "listFristen").mockResolvedValue([]);
    renderRoutes("/kautionen", role);
    expect(await screen.findByRole("heading", { name: "Kautionen" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Kautionen" })).toHaveAttribute("href", "/kautionen");
  });
});

describe("governance navigation", () => {
  it("hides advanced governance links from clerks", async () => {
    renderRoutes("/objekte", "CLERK");
    expect(await screen.findByRole("heading", { name: "Objekte" })).toBeVisible();
    expect(screen.queryByText("Erweitert")).toBeNull();
    for (const name of ["Quellenfreigaben", "Altquellen", "Berechtigungen", "Freigaben vorbereiten", "Mahnquarantäne", "Mandatsquarantäne", "Seitenauszüge"]) {
      expect(screen.queryByRole("link", { name })).toBeNull();
    }
  });

  it("keeps advanced governance links reachable for admins", async () => {
    renderRoutes("/objekte", "OWNER_ADMIN");
    expect(await screen.findByRole("link", { name: "Berechtigungen" })).toBeVisible();
    expect(screen.getByText("Erweitert")).toBeVisible();
  });
});


describe("report navigation", () => {
  it.each(["READ_ONLY", "CLERK"])("opens reports and the Berichte sidebar link for %s", async role => {
    vi.spyOn(endpoints, "listProperties").mockResolvedValue([]);
    renderRoutes("/berichte", role);
    expect(await screen.findByRole("heading", { name: "Berichte" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Berichte" })).toHaveAttribute("href", "/berichte");
    expect(screen.getByLabelText("Objekt *")).toBeVisible();
  });
});

describe("special levy navigation", () => {
  it.each(["READ_ONLY", "CLERK"])("opens Sonderumlagen in WEG navigation for %s", async role => {
    vi.spyOn(endpoints, "listProperties").mockResolvedValue([]);
    vi.spyOn(endpoints, "listUnits").mockResolvedValue([]);
    vi.spyOn(endpoints, "listOwnerProfiles").mockResolvedValue([]);
    vi.spyOn(endpoints, "listPersons").mockResolvedValue([]);
    vi.spyOn(endpoints, "listFristen").mockResolvedValue([]);
    renderRoutes("/sonderumlagen", role);
    expect(await screen.findByRole("heading", { name: "Sonderumlagen" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Sonderumlagen" })).toHaveAttribute("href", "/sonderumlagen");
  });
});


describe("task navigation", () => {
  it.each(["READ_ONLY", "CLERK", "OWNER_ADMIN"])("opens Aufgaben for %s without finance scope", async role => {
    vi.spyOn(endpoints, "listTasks").mockResolvedValue({ items: [], next_offset: null });
    vi.spyOn(endpoints, "listProperties").mockResolvedValue([]);
    vi.spyOn(endpoints, "listUnits").mockResolvedValue([]);
    vi.spyOn(endpoints, "listLeases").mockResolvedValue([]);
    vi.spyOn(endpoints, "listTaskAssignees").mockResolvedValue([]);
    renderRoutes("/aufgaben", role);
    expect(await screen.findByRole("heading", { name: "Aufgaben" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Aufgaben" })).toHaveAttribute("href", "/aufgaben");
  });
});

describe("letter navigation", () => {
  it.each(["READ_ONLY", "CLERK", "OWNER_ADMIN"])("opens Briefe for %s", async role => {
    vi.spyOn(endpoints, "listProperties").mockResolvedValue([]);
    vi.spyOn(letters, "listLetterTemplates").mockResolvedValue([]);
    renderRoutes("/briefe", role);
    expect(await screen.findByRole("heading", { name: "Briefe" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Briefe" })).toHaveAttribute("href", "/briefe");
  });
});


describe("permissions area", () => {
  it.each([
    ["PREPARATION", "Vorbereitung", "Prüfen Sie zuerst Teamrechte und Bestand"],
    ["ENFORCED", "Zugriffsregeln aktiv", "Prüfen Sie offene Freigaben und Quarantänen"],
    ["PAUSED", "Aktionen pausiert", "Prüfen Sie vor der Wiederaufnahme einen neuen Prüfstand"],
  ])("explains %s with the next step and inventory counts", async (mode, label, next) => {
    permissionsMode = mode;
    renderRoutes("/berechtigungen", "OWNER_ADMIN");
    expect(await screen.findByText(label)).toBeVisible();
    expect(screen.getByText(new RegExp(next))).toBeVisible();
    expect(screen.getByText(/Mitarbeitende/)).toBeVisible();
    expect(await screen.findByText("100")).toBeVisible();
    for (const gap of ["Kontakte mit unbekanntem Ersteller", "Nicht zugeordnete Originale mit unbekannter Herkunft"]) {
      expect(screen.getByText(gap).parentElement).toHaveTextContent("1");
    }
    expect(screen.getByRole("link", { name: "Teamrechte" })).toHaveAttribute("href", "/berechtigungen/teamrechte");
    expect(screen.getByRole("link", { name: "Bestand prüfen" })).toHaveAttribute("href", "/berechtigungen/bestand");
    expect(screen.getByRole("link", { name: "Offene Prüfungen" })).toHaveAttribute("href", "/berechtigungen/pruefungen");
    for (const raw of ["PREPARATION", "ENFORCED", "PAUSED"]) expect(document.body).not.toHaveTextContent(raw);
    for (const name of ["Kontaktdaten-Rechte", "Gebäudedaten-Rechte"]) expect(screen.getByRole("link", { name })).toBeVisible();
  });

  it("keeps unavailable status distinct from inventory and retries without stale advice", async () => {
    permissionsStatusFails = true;
    renderRoutes("/berechtigungen", "OWNER_ADMIN");
    expect(await screen.findByRole("alert")).toHaveTextContent("Quellschutz ist nicht verfügbar");
    expect(await screen.findByText("100")).toBeVisible();
    expect(screen.queryByText("Nächster Schritt")).toBeNull();
    permissionsStatusFails = false;
    permissionsInventoryFails = true;
    permissionsMode = "PAUSED";
    fireEvent.click(screen.getByRole("button", { name: "Status und Bestand neu laden" }));
    expect(await screen.findByText("Aktionen pausiert")).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent("Bestandszahlen sind nicht verfügbar");
    expect(screen.queryByText("100")).toBeNull();
  });

  it("shows all three review lists with loaded counts in one section", async () => {
    renderRoutes("/berechtigungen/pruefungen", "OWNER_ADMIN");
    for (const label of ["Quellenfreigaben: 0 geladen", "Mahnungen: 0 geladen", "Mandatsunterlagen: 0 geladen"]) {
      expect(await screen.findByText(label)).toBeVisible();
    }
  });

  it.each([
    ["/freigaben-vorbereiten", "/berechtigungen/bestand", "Bestand prüfen"],
    ["/quellenfreigaben", "/berechtigungen/pruefungen/quellen", "Quellenfreigaben"],
    ["/quellenfreigaben/review-1", "/berechtigungen/pruefungen/quellen/review-1", "Quellenfreigaben"],
    ["/mahnquarantaene", "/berechtigungen/pruefungen/mahnungen", "Alte Mahnungen prüfen"],
    ["/mandatsquarantaene", "/berechtigungen/pruefungen/mandate", "Mandatsquarantäne prüfen"],
    ["/berechtigungen/scope-1", "/berechtigungen/teamrechte/scope-1", "Teamrechte"],
  ])("redirects %s into the area preserving detail references", async (old, target, heading) => {
    renderRoutes(old, "OWNER_ADMIN");
    expect(await screen.findByRole("heading", { name: heading })).toBeVisible();
    expect(screen.getByLabelText("Aktuelle Route")).toHaveTextContent(target);
  });

  it("does not fetch administrative status or inventory for a clerk", async () => {
    renderRoutes("/berechtigungen", "CLERK");
    expect(await screen.findByText("Zugriff nur für Inhaber/Admin.")).toBeVisible();
    expect(api.get).not.toHaveBeenCalledWith("/api/access-preparation");
    expect(api.get).not.toHaveBeenCalledWith("/api/access-preparation/source-security");
  });
});
