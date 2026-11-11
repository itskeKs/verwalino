import { StrictMode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { InvitationAcceptPage } from "./InvitationAcceptPage";

const tokenKey = "verwalino.invitation-token";
const password = "A-new-password-123";
function Location() { return <span data-testid="location">{useLocation().search}</span>; }
function open(path = "/einladung?token=private-token") {
  return render(<StrictMode><MemoryRouter initialEntries={[path]}><Location /><Routes>
    <Route path="/einladung" element={<InvitationAcceptPage />} />
  </Routes></MemoryRouter></StrictMode>);
}
function fill(repeat = password) {
  fireEvent.change(screen.getByLabelText("Passwort", { exact: true }), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("Passwort wiederholen"), { target: { value: repeat } });
}
beforeEach(() => {
  sessionStorage.clear();
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(null, { status: 204 })));
});
it("keeps the query token through reload, posts once on submit and clears it after success", async () => {
  const first = open();
  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(""));
  expect(sessionStorage.getItem(tokenKey)).toBe("private-token");
  expect(fetch).not.toHaveBeenCalled();
  first.unmount(); open("/einladung"); fill();
  await userEvent.click(screen.getByRole("button", { name: "Passwort festlegen" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Zugang ist eingerichtet");
  expect(fetch).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledWith("/api/auth/invitation/accept", expect.objectContaining({ body: JSON.stringify({ token: "private-token", password }) }));
  expect(sessionStorage.getItem(tokenKey)).toBeNull();
  expect(screen.getByRole("link", { name: "Zur Anmeldung" })).toHaveAttribute("href", "/login");
});
it("explains mismatching passwords before submitting", async () => {
  open(); fill("different-password-123");
  await userEvent.click(screen.getByRole("button", { name: "Passwort festlegen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("stimmen nicht überein");
  expect(fetch).not.toHaveBeenCalled();
});
it("keeps the stored token when the accept request is throttled", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify({ detail: "Zu viele Versuche. Bitte versuchen Sie es später erneut." }), { status: 429 })));
  open(); fill();
  await userEvent.click(screen.getByRole("button", { name: "Passwort festlegen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Zu viele Versuche");
  expect(sessionStorage.getItem(tokenKey)).toBe("private-token");
});
