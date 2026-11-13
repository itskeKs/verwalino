import { AppRoutes } from "../App";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "../auth/AuthContext";
import { LoginPage } from "./LoginPage";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("LoginPage", () => {
  it("shows the generic API error and disables submit while pending", async () => {
    let resolveLogin: ((response: Response) => void) | undefined;
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse({ detail: "Invalid credentials" }, 401))
        .mockImplementationOnce(
          () => new Promise<Response>((resolve) => {
            resolveLogin = resolve;
          }),
        ),
    );
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <LoginPage />
      </AuthProvider>,
    );

    await user.type(screen.getByLabelText("E-Mail"), "alpha@example.test");
    await user.type(screen.getByLabelText("Passwort"), "not-the-right-password");
    await user.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(screen.getByRole("button", { name: "Anmeldung läuft …" })).toBeDisabled();
    expect(screen.getByRole("form", { name: "Anmelden" })).toHaveAttribute("aria-busy", "true");
    expect(screen.getByLabelText("E-Mail")).toBeDisabled();
    expect(screen.getByLabelText("Passwort")).toBeDisabled();

    resolveLogin?.(jsonResponse({ detail: "Invalid credentials" }, 401));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid credentials");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveFocus());
    expect(screen.getByLabelText("E-Mail")).toHaveValue("alpha@example.test");
    expect(screen.getByLabelText("Passwort")).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByRole("button", { name: "Anmelden" })).toBeEnabled();
  });

  it("asks for the code after the password of an enrolled account and shows the generic refusal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
        .mockResolvedValueOnce(
          jsonResponse({
            user_id: "u1",
            organization_id: "o1",
            role: "CLERK",
            second_factor_required: true,
            second_factor_enrolment_required: false,
            pending_token: "pending-token",
          }),
        )
        .mockResolvedValueOnce(jsonResponse({ detail: "Invalid credentials" }, 401)),
    );
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <LoginPage />
      </AuthProvider>,
    );

    await user.type(screen.getByLabelText("E-Mail"), "sabine@example.test");
    await user.type(screen.getByLabelText("Passwort"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(await screen.findByLabelText("6-stelliger Code")).toBeInTheDocument();
    expect(screen.getByText(/sabine@example\.test/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("6-stelliger Code"), "123456");
    await user.click(screen.getByRole("button", { name: "Anmelden" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid credentials");
    // A wrong code is refused exactly like a wrong password, with the same message.
  });

  it("offers the one-time recovery code when the authenticator app is not at hand", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn()
        .mockResolvedValueOnce(jsonResponse({ detail: "Not authenticated" }, 401))
        .mockResolvedValueOnce(
          jsonResponse({
            user_id: "u1",
            organization_id: "o1",
            role: "CLERK",
            second_factor_required: true,
            second_factor_enrolment_required: false,
            pending_token: "pending-token",
          }),
        ),
    );
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <LoginPage />
      </AuthProvider>,
    );

    await user.type(screen.getByLabelText("E-Mail"), "sabine@example.test");
    await user.type(screen.getByLabelText("Passwort"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: "Anmelden" }));

    await user.click(await screen.findByRole("button", { name: "Authenticator-App nicht zur Hand?" }));
    expect(screen.getByLabelText("Wiederherstellungscode")).toBeInTheDocument();
  });
});

it.each([true, false])("offers password recovery only with available mail (%s)", (available) => {
  render(<MemoryRouter><AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout: vi.fn() }}><LoginPage mailAvailable={available} /></AuthCtx.Provider></MemoryRouter>);
  if (available) expect(screen.getByRole("link", { name: "Passwort vergessen?" })).toHaveAttribute("href", "/passwort-vergessen");
  else expect(screen.queryByRole("link", { name: "Passwort vergessen?" })).not.toBeInTheDocument();
});

it.each([true, false, undefined])("uses public metadata to show or hide recovery (%s)", async (available) => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url) => jsonResponse(url === "/api/meta"
    ? { deployment_mode: "selfhost", mail_available: available }
    : { required: false })));
  render(<MemoryRouter initialEntries={["/login"]}><AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout: vi.fn() }}><AppRoutes /></AuthCtx.Provider></MemoryRouter>);
  await screen.findByRole("button", { name: "Anmelden" });
  if (available === true) expect(screen.getByRole("link", { name: "Passwort vergessen?" })).toBeInTheDocument();
  else expect(screen.queryByRole("link", { name: "Passwort vergessen?" })).not.toBeInTheDocument();
});
