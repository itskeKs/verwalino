import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { StrictMode } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { SignupPage, SignupConfirmPage } from "./SignupPage";
import { AppRoutes } from "../App";
import { AuthCtx } from "../auth/useAuth";

const complete = vi.fn();
const password = "A-long-signup-password-123";
const message = "Wenn die Adresse frei ist, haben wir eine E-Mail geschickt";
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
beforeEach(() => {
  complete.mockReset();
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url) => json(url === "/api/meta" ? { deployment_mode: "hosted", version: "0.1.0" } : { message })));
});
function open(path = "/signup") {
  return render(<StrictMode><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/signup" element={<SignupPage />} />
    <Route path="/signup/confirm" element={<SignupConfirmPage onComplete={complete} />} />
    <Route path="/login" element={<p>Anmeldung</p>} />
  </Routes></MemoryRouter></StrictMode>);
}
async function fill(repeat = password) {
  await screen.findByRole("heading", { name: "Konto erstellen" });
  for (const [label, value] of [["Organisation", "Hausverwaltung mit einem sehr langen deutschen Organisationsnamen GmbH"], ["Name", "Erika Mustermann"], ["E-Mail", "erika@example.test"], ["Passwort", password], ["Passwort wiederholen", repeat]]) {
    fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } });
  }
}
it("submits signup once, shows the generic receipt and requests a resend", async () => {
  open(); await fill();
  await userEvent.click(screen.getByRole("button", { name: "Konto erstellen" }));
  expect(await screen.findByRole("status")).toHaveTextContent(message);
  const signup = vi.mocked(fetch).mock.calls.find(([url]) => url === "/api/signup");
  expect(JSON.parse(signup![1]!.body as string)).toMatchObject({ admin_name: "Erika Mustermann", email: "erika@example.test", password, website: "" });
  expect(screen.queryByLabelText("Passwort", { exact: true })).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Erneut senden" }));
  expect(vi.mocked(fetch).mock.calls.find(([url]) => url === "/api/signup/resend")?.[1]).toMatchObject({ method: "POST", body: JSON.stringify({ email: "erika@example.test" }) });
  expect(await screen.findByRole("status")).toHaveTextContent(message);
});
it("explains mismatching passwords without submitting and preserves failed form input", async () => {
  open(); await fill("Different-password-123");
  await userEvent.click(screen.getByRole("button", { name: "Konto erstellen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Passwörter stimmen nicht überein");
  expect(vi.mocked(fetch).mock.calls.every(([url]) => url === "/api/meta")).toBe(true);
  fireEvent.change(screen.getByLabelText("Passwort wiederholen"), { target: { value: password } });
  vi.mocked(fetch).mockResolvedValueOnce(json({ detail: "Registrierung derzeit nicht verfügbar." }, 503));
  await userEvent.click(screen.getByRole("button", { name: "Konto erstellen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("derzeit nicht verfügbar");
  expect(screen.getByLabelText("Name", { exact: true })).toHaveValue("Erika Mustermann");
});
it.each(["selfhost", null])("hides signup in mode %s and does not post a token", async (mode) => {
  vi.mocked(fetch).mockResolvedValue(json({ deployment_mode: mode }));
  open("/signup/confirm?token=private-token");
  expect(await screen.findByText("Anmeldung")).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.every(([url]) => url === "/api/meta")).toBe(true);
});
it("confirms via one POST even in StrictMode and continues with the cookie", async () => {
  open("/signup/confirm?token=private-token");
  const chosen = await screen.findByLabelText("Passwort", { exact: true });
  expect(vi.mocked(fetch).mock.calls.filter(([url]) => url === "/api/signup/confirm")).toHaveLength(0);
  fireEvent.change(chosen, { target: { value: password } });
  const form = screen.getByRole("button", { name: "E-Mail bestätigen" }).closest("form")!;
  fireEvent.submit(form);
  fireEvent.submit(form);
  await waitFor(() => expect(complete).toHaveBeenCalledOnce());
  const requests = vi.mocked(fetch).mock.calls.filter(([url]) => url === "/api/signup/confirm");
  expect(requests).toHaveLength(1);
  expect(requests[0][1]).toMatchObject({ method: "POST", credentials: "include", body: JSON.stringify({ token: "private-token", password }) });
  expect((window.localStorage?.length ?? 0)).toBe(0);
  expect((window.sessionStorage?.length ?? 0)).toBe(0);
});
it("reports a refused confirmation and offers a new registration", async () => {
  vi.mocked(fetch).mockImplementation(async (url) => url === "/api/meta" ? json({ deployment_mode: "hosted" }) : json({ detail: "Bestätigung nicht möglich. Bitte fordern Sie einen neuen Link an." }, 403));
  open("/signup/confirm?token=private-token");
  fireEvent.change(await screen.findByLabelText("Passwort", { exact: true }), { target: { value: password } });
  await userEvent.click(screen.getByRole("button", { name: "E-Mail bestätigen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Bestätigung nicht möglich");
  expect(screen.getByRole("link", { name: "Zur Registrierung" })).toHaveAttribute("href", "/signup");
  expect(complete).not.toHaveBeenCalled();
});
it.each(["selfhost", "hosted"])("offers the login signup link only in hosted mode (%s)", async (mode) => {
  vi.mocked(fetch).mockImplementation(async (url) => json(url === "/api/meta" ? { deployment_mode: mode } : { required: false }));
  render(<AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/login"]}><AppRoutes /></MemoryRouter></AuthCtx.Provider>);
  await screen.findByRole("button", { name: "Anmelden" });
  if (mode === "hosted") expect(screen.getByRole("link", { name: "Konto erstellen" })).toHaveAttribute("href", "/signup");
  else expect(screen.queryByRole("link", { name: "Konto erstellen" })).not.toBeInTheDocument();
});
it("routes unauthenticated hosted visitors to the signup form", async () => {
  render(<AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/signup"]}><AppRoutes /></MemoryRouter></AuthCtx.Provider>);
  expect(await screen.findByLabelText("Organisation")).toBeInTheDocument();
});
