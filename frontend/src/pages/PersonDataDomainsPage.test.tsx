import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { PersonDataDomainsPage } from "./PersonDataDomainsPage";
import { api } from "../api/client";

vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: "OWNER_ADMIN" } }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderPage(path: string) {
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/kontaktdaten-rechte" element={<PersonDataDomainsPage />} /><Route path="/kontaktdaten-rechte/:domainId" element={<PersonDataDomainsPage />} /></Routes></MemoryRouter>);
}

it("retains the creation key after a lost response and clearly labels preparation", async () => {
  vi.spyOn(api, "get").mockImplementation(async url => url.includes("/persons/page")
    ? { items: [{ id: "person", first_name: "Synthetic", last_name: "Contact" }], next_cursor: null }
    : { items: [], next_cursor: null });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Reply lost"))
    .mockResolvedValue({ id: "domain", label: "Contact person", enforcement_status: "PREPARATION" });
  renderPage("/kontaktdaten-rechte");
  expect(screen.getByText(/beschränken den bisherigen Zugriff noch nicht/)).toBeVisible();
  await screen.findByRole("option", { name: /Synthetic Contact/ });
  fireEvent.change(screen.getByLabelText("Kontakt", { selector: "select" }), { target: { value: "person" } });
  fireEvent.change(screen.getByLabelText("Begründung"), { target: { value: "TEAM_ACCESS" } });
  fireEvent.click(screen.getByRole("button", { name: "Datenbereich vorbereiten" }));
  await screen.findByText("Error: Reply lost");
  fireEvent.click(screen.getByRole("button", { name: "Datenbereich vorbereiten" }));
  await screen.findByRole("link", { name: /Contact person – Teamfreigaben/ });
  expect(post.mock.calls[0][2]?.headers).toEqual(post.mock.calls[1][2]?.headers);
  expect(post.mock.calls[1][1]).not.toHaveProperty("reason");
  expect(post.mock.calls[1][1]).not.toHaveProperty("label");
});

it("retries a grant with one key, then reloads durable revocation", async () => {
  let revoked = false;
  let granted = false;
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url.includes("/users/page")) return { items: [{ id: "member", display_name: "Clerk", email: "clerk@example.test" }], next_cursor: null };
    if (url.includes("/grants/page")) return { items: granted ? [{ id: "grant", user_display_name: "Clerk", capabilities: ["read"], reason_code: "TEAM_ACCESS", revoked_at: revoked ? "2026-10-02T12:00:00Z" : null, expires_at: null }] : [], next_cursor: null };
    return { id: "domain", label: "Contact person", person_id: "person", reason_code: "INITIAL_SETUP", enforcement_status: "PREPARATION" };
  });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Reply lost")).mockImplementation(async url => {
    if (url.endsWith("/revoke")) revoked = true;
    else granted = true;
    return { id: "grant" };
  });
  renderPage("/kontaktdaten-rechte/domain");
  await screen.findByRole("heading", { name: "Contact person" });
  expect(screen.queryByLabelText("Freigeben")).not.toBeInTheDocument();
  await screen.findByRole("option", { name: "Clerk (clerk@example.test)" });
  fireEvent.change(screen.getByLabelText("Teammitglied", { selector: "select" }), { target: { value: "member" } });
  fireEvent.change(screen.getByLabelText("Begründung"), { target: { value: "TEAM_ACCESS" } });
  fireEvent.click(screen.getByRole("button", { name: "Teamfreigabe vorbereiten" }));
  await screen.findByText("Error: Reply lost");
  fireEvent.click(screen.getByRole("button", { name: "Teamfreigabe vorbereiten" }));
  await screen.findByText("Vorbereitet ohne Ablauf");
  expect(post.mock.calls[0][2]?.headers).toEqual(post.mock.calls[1][2]?.headers);
  expect(post.mock.calls[1][1]).not.toHaveProperty("reason");
  expect(post.mock.calls[1][1]).not.toHaveProperty("label");
  expect(post.mock.calls[1][1]).toMatchObject({ capabilities: ["read"] });
  fireEvent.click(screen.getByRole("button", { name: "Widerrufen" }));
  await screen.findByText("Widerrufen");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Widerrufen" })).not.toBeInTheDocument());
});
