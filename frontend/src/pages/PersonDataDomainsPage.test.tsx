import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { PersonDataDomainsPage } from "./PersonDataDomainsPage";
import { api } from "../api/client";

vi.mock("../auth/useAuth", () => ({
  useAuth: () => ({ session: { user_id: "actor", organization_id: "org", role: "OWNER_ADMIN" } }),
}));
vi.mock("../api/client", () => ({
  api: {
    get: vi.fn(async () => ({ id: "domain", person_id: "person", label: "Kontaktbereich", reason_code: "INITIAL_SETUP", enforcement_status: "PREPARATION" })),
    post: vi.fn(),
  },
}));
vi.mock("../hooks/usePagedList", () => ({
  usePagedList: () => ({ items: [], query: "", setQuery: vi.fn(), loading: false,
    error: null, nextCursor: null, reload: vi.fn(), loadMore: vi.fn() }),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

describe("Contact rights reason accessibility", () => {
  it.each([
    ["/kontaktdaten-rechte", "INITIAL_SETUP"],
    ["/kontaktdaten-rechte/domain", "TEAM_ACCESS"],
  ])("names the native reason selector exactly on %s", async (url, initial) => {
    render(<MemoryRouter initialEntries={[url]}><Routes>
      <Route path="/kontaktdaten-rechte" element={<PersonDataDomainsPage />} />
      <Route path="/kontaktdaten-rechte/:domainId" element={<PersonDataDomainsPage />} />
    </Routes></MemoryRouter>);
    if (initial === "TEAM_ACCESS") await screen.findByText("Kontaktbereich");
    const select = screen.getByRole("combobox", { name: /^Begründung$/ });
    expect(select).toHaveValue(initial);
    expect(select.querySelectorAll("option")).toHaveLength(4);
    fireEvent.change(select, { target: { value: "TEMPORARY_ACCESS" } });
    expect(select).toHaveValue("TEMPORARY_ACCESS");
    expect(api.post).not.toHaveBeenCalled();
  });
});
