import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { AppRoutes } from "./App";
import * as endpoints from "./api/endpoints";

function renderRoutes(path: string, role?: string) {
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
  vi.spyOn(endpoints, "listProperties").mockResolvedValue([]);
  vi.spyOn(endpoints, "getPropertyKnowledge").mockResolvedValue({ property_id: "property-1", documents: [], document_relations: [], rules_by_topic: {}, open_rule_topics: [] });
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><AppRoutes /></AuthProvider></MemoryRouter>);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
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
