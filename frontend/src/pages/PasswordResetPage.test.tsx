import { StrictMode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { PasswordResetPage } from "./PasswordResetPage";

const tokenKey = "verwalino.password-reset-token";
const password = "A-new-password-123";
function Location() { return <span data-testid="location">{useLocation().search}</span>; }
function open(path = "/passwort-zuruecksetzen?token=private-token") {
  return render(<StrictMode><MemoryRouter initialEntries={[path]}><Location /><Routes>
    <Route path="/passwort-zuruecksetzen" element={<PasswordResetPage />} />
  </Routes></MemoryRouter></StrictMode>);
}
function fill(repeat = password) {
  fireEvent.change(screen.getByLabelText("Neues Passwort", { exact: true }), { target: { value: password } });
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
  first.unmount(); open("/passwort-zuruecksetzen"); fill();
  await userEvent.click(screen.getByRole("button", { name: "Passwort speichern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Ihr Passwort wurde geändert");
  expect(fetch).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledWith("/api/auth/password-reset/confirm", expect.objectContaining({ body: JSON.stringify({ token: "private-token", password }) }));
  expect(sessionStorage.getItem(tokenKey)).toBeNull();
  expect(screen.getByRole("link", { name: "Zur Anmeldung" })).toHaveAttribute("href", "/login");
});
it("explains mismatching passwords before submitting", async () => {
  open(); fill("different-password-123");
  await userEvent.click(screen.getByRole("button", { name: "Passwort speichern" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("stimmen nicht überein");
  expect(fetch).not.toHaveBeenCalled();
});
it.each([403, 503])("handles a refusal or retryable failure (%s)", async (status) => {
  vi.mocked(fetch).mockImplementation(async () => new Response(JSON.stringify({ detail: "Zurücksetzen nicht möglich. Bitte fordern Sie einen neuen Link an." }), { status }));
  open(); fill();
  await userEvent.click(screen.getByRole("button", { name: "Passwort speichern" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Zurücksetzen nicht möglich");
  expect(sessionStorage.getItem(tokenKey)).toBe(status === 403 ? null : "private-token");
  expect(screen.getByRole("link", { name: "Neuen Link anfordern" })).toHaveAttribute("href", "/passwort-vergessen");
});
it("shows backend policy errors and retains the token for a corrected Unicode password", async () => {
  const detail = "Das Passwort muss zwischen 12 und 128 Zeichen lang sein.";
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ detail }), { status: 422 }));
  open();
  for (const label of ["Neues Passwort", "Passwort wiederholen"]) {
    const input = screen.getByLabelText(label, { exact: true });
    expect(input).not.toHaveAttribute("minlength");
    expect(input).not.toHaveAttribute("maxlength");
    fireEvent.change(input, { target: { value: "😀".repeat(6) } });
  }
  await userEvent.click(screen.getByRole("button", { name: "Passwort speichern" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(detail);
  expect(sessionStorage.getItem(tokenKey)).toBe("private-token");
  fill();
  await userEvent.click(screen.getByRole("button", { name: "Passwort speichern" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Ihr Passwort wurde geändert");
});
