import { api } from "./api/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { AppRoutes } from "./App";
import * as endpoints from "./api/endpoints";
import * as letters from "./api/letters";

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
    return Promise.resolve({ items: [], next_cursor: null });
  });
  vi.spyOn(endpoints, "getPropertyKnowledge").mockResolvedValue({ property_id: "property-1", documents: [], document_relations: [], rules_by_topic: {}, open_rule_topics: [] });
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><AppRoutes /></AuthProvider></MemoryRouter>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
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
    expect(await screen.findByRole("link", { name: "Quellenfreigaben" })).toBeVisible();
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
