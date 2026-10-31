import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AccountingScopesPage } from "./AccountingScopesPage";
import { api } from "../api/client";

vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: "OWNER_ADMIN" } }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("retries a lost grant reply with one key and starts a new explicit grant with a fresh key", async () => {
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url.endsWith("/control")) return { mode: "compatibility", event: null };
    if (url.includes("/users/page")) {
      expect(url).toContain("eligible_for_scope=true");
      return { items: [{ id: "member", display_name: "Clerk", email: "clerk@example.test" }], next_cursor: null };
    }
    if (url.includes("/grants/page")) return { items: [], next_cursor: null };
    return { id: "sphere", label: "Rental A", scope_type: "MIET", property_id: "property", valid_from: "2025-01-01", valid_to: null };
  });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Reply lost"))
    .mockResolvedValue({ id: "grant", revoked_at: null });
  render(<MemoryRouter initialEntries={["/berechtigungen/sphere"]}><Routes>
    <Route path="/berechtigungen/:scopeId" element={<AccountingScopesPage />} />
  </Routes></MemoryRouter>);
  await screen.findByRole("heading", { name: "Rental A (MIET)" });
  expect(screen.getByRole("heading", { name: "Teamrechte" })).toBeVisible();
  expect(screen.queryByText(/Teambetrieb ist noch nicht aktiviert/)).toBeNull();
  expect(screen.getByRole("link", { name: "Alle Teamrechte" })).toHaveAttribute("href", "/berechtigungen/teamrechte");
  const user = await screen.findByLabelText("Teammitglied", { selector: "select" });
  await screen.findByRole("option", { name: "Clerk (clerk@example.test)" });
  fireEvent.change(user, { target: { value: "member" } });
  fireEvent.change(screen.getByLabelText("Begründung"), { target: { value: "Reviewed assignment" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "Vorbereiten" }));
  fireEvent.click(screen.getByRole("button", { name: "Freigabe erteilen" }));
  await screen.findByText("Error: Reply lost");
  fireEvent.click(screen.getByRole("button", { name: "Freigabe erteilen" }));
  await waitFor(() => expect(user).toHaveValue(""));
  expect(post).toHaveBeenCalledTimes(2);
  expect(post.mock.calls[0][2]?.headers).toEqual(post.mock.calls[1][2]?.headers);
  expect(post.mock.calls[1][1]).toMatchObject({ capabilities: ["prepare", "read"] });
  fireEvent.change(user, { target: { value: "member" } });
  fireEvent.change(screen.getByLabelText("Begründung"), { target: { value: "Reviewed assignment" } });
  fireEvent.click(screen.getByRole("button", { name: "Freigabe erteilen" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
  expect(post.mock.calls[2][2]?.headers).not.toEqual(post.mock.calls[1][2]?.headers);
});
