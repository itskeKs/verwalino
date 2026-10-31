import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { PropertyDataDomainsPage } from "./PropertyDataDomainsPage";
import { api } from "../api/client";

vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: "OWNER_ADMIN" } }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderPage(path: string) {
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/gebaeudedaten-rechte" element={<PropertyDataDomainsPage />} /><Route path="/gebaeudedaten-rechte/:domainId" element={<PropertyDataDomainsPage />} /></Routes></MemoryRouter>);
}

it("retains the creation key after a lost response and explains when the grants restrict access", async () => {
  vi.spyOn(api, "get").mockImplementation(async url => url.includes("/properties/page")
    ? { items: [{ id: "property", name: "House", management_type: "WEG" }], next_cursor: null }
    : { items: [], next_cursor: null });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Reply lost"))
    .mockResolvedValue({ id: "domain", label: "Shared house", enforcement_status: "PREPARATION" });
  renderPage("/gebaeudedaten-rechte");
  expect(screen.getByText(/Diese Freigaben bestimmen bei aktivem Quellschutz/)).toBeVisible();
  await screen.findByRole("option", { name: /House/ });
  fireEvent.change(screen.getByLabelText("Objekt", { selector: "select" }), { target: { value: "property" } });
  fireEvent.change(screen.getByLabelText("Bezeichnung"), { target: { value: "Shared house" } });
  fireEvent.change(screen.getByLabelText("Begründung"), { target: { value: "Reviewed audience" } });
  fireEvent.click(screen.getByRole("button", { name: "Datenbereich vorbereiten" }));
  await screen.findByText("Error: Reply lost");
  fireEvent.click(screen.getByRole("button", { name: "Datenbereich vorbereiten" }));
  await screen.findByRole("link", { name: /Shared house – Teamfreigaben/ });
  expect(post.mock.calls[0][2]?.headers).toEqual(post.mock.calls[1][2]?.headers);
});

it("retries a grant with one key, then reloads durable revocation", async () => {
  let revoked = false;
  let granted = false;
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url.includes("/users/page")) return { items: [{ id: "member", display_name: "Clerk", email: "clerk@example.test" }], next_cursor: null };
    if (url.includes("/grants/page")) return { items: granted ? [{ id: "grant", user_display_name: "Clerk", capabilities: ["read"], reason: "Reviewed audience", revoked_at: revoked ? "2026-10-02T12:00:00Z" : null, expires_at: null }] : [], next_cursor: null };
    return { id: "domain", label: "Shared house", property_id: "property", reason: "Shared building evidence", enforcement_status: "PREPARATION" };
  });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Reply lost")).mockImplementation(async url => {
    if (url.endsWith("/revoke")) revoked = true;
    else granted = true;
    return { id: "grant" };
  });
  renderPage("/gebaeudedaten-rechte/domain");
  await screen.findByRole("heading", { name: "Shared house" });
  await screen.findByRole("option", { name: "Clerk (clerk@example.test)" });
  fireEvent.change(screen.getByLabelText("Teammitglied", { selector: "select" }), { target: { value: "member" } });
  fireEvent.change(screen.getByLabelText("Begründung"), { target: { value: "Reviewed audience" } });
  fireEvent.click(screen.getByRole("button", { name: "Teamfreigabe vorbereiten" }));
  await screen.findByText("Error: Reply lost");
  fireEvent.click(screen.getByRole("button", { name: "Teamfreigabe vorbereiten" }));
  await screen.findByText("Vorbereitet ohne Ablauf");
  expect(post.mock.calls[0][2]?.headers).toEqual(post.mock.calls[1][2]?.headers);
  expect(post.mock.calls[1][1]).toMatchObject({ capabilities: ["read"] });
  fireEvent.click(screen.getByRole("button", { name: "Widerrufen" }));
  await screen.findByText("Widerrufen");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Widerrufen" })).not.toBeInTheDocument());
});
