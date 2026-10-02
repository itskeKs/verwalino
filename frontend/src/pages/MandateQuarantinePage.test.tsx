import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MandateQuarantinePage } from "./MandateQuarantinePage";
import { api } from "../api/client";
import * as auth from "../auth/useAuth";

const row = { id: "original-id", document_id: "original-id", snapshot_digest: "a".repeat(64), observed_at: "2026-10-02T12:00:00Z", disposition: null };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
function show(role: "OWNER_ADMIN" | "CLERK" = "OWNER_ADMIN") {
  vi.spyOn(auth, "useAuth").mockReturnValue({ loading: false, session: { user_id: "actor", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() });
  render(<MemoryRouter><MandateQuarantinePage /></MemoryRouter>);
}

it("requires explicit confirmation and reuses the request key after a lost response", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [row], next_cursor: null });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Response lost")).mockResolvedValueOnce({ id: "receipt", document_id: row.id, reason: "CUSTODY_UNVERIFIED", reviewed_at: row.observed_at });
  show();
  const button = await screen.findByRole("button", { name: "Quarantäne als geprüft vermerken" });
  expect(button).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(button); await screen.findByRole("alert");
  fireEvent.click(button); await screen.findByText(/Quarantäne geprüft ·/);
  expect(post.mock.calls[0][2]).toEqual(post.mock.calls[1][2]);
  expect(post.mock.calls[0][2]?.headers).toHaveProperty("Idempotency-Key");
  expect(screen.queryByRole("button", { name: "Quarantäne als geprüft vermerken" })).toBeNull();
});

it("clears stale observations after a failed reload and retries", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValueOnce({ items: [row], next_cursor: null }).mockRejectedValueOnce(new Error("Unavailable")).mockResolvedValueOnce({ items: [row], next_cursor: null });
  show();await screen.findByRole("heading", { name: "Original original-id" });
  fireEvent.click(screen.getByRole("button", { name: "Quarantäne neu laden" }));await screen.findByRole("alert");
  expect(screen.queryByRole("heading", { name: "Original original-id" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Quarantäne neu laden" }));await screen.findByRole("heading", { name: "Original original-id" });
  await waitFor(() => expect(get).toHaveBeenCalledTimes(3));
});

it("never fetches the administrative inventory for a clerk", () => {
  const get = vi.spyOn(api, "get");show("CLERK");
  expect(screen.getByText("Zugriff nur für Inhaber/Admin.")).toBeVisible();expect(get).not.toHaveBeenCalled();
});


it("uses the server disposition after refreshing a lost response", async () => {
  let reviewed = false;
  vi.spyOn(api, "get").mockImplementation(async () => ({ items: [{ ...row, disposition: reviewed ? {
    id: "receipt", document_id: row.id, reason: "CUSTODY_UNVERIFIED", reviewed_at: row.observed_at,
  } : null }], next_cursor: null }));
  vi.spyOn(api, "post").mockImplementation(async () => { reviewed = true; throw new Error("Response lost after commit"); });
  show();await screen.findByRole("button", { name: "Quarantäne als geprüft vermerken" });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Quarantäne als geprüft vermerken" }));await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Quarantäne neu laden" }));
  await screen.findByText(/Quarantäne geprüft ·/);
  expect(screen.queryByRole("button", { name: "Quarantäne als geprüft vermerken" })).toBeNull();
});
