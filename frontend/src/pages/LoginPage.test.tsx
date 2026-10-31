import { AppRoutes } from "../App";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { render, screen } from "@testing-library/react";
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

    expect(screen.getByRole("button", { name: "..." })).toBeDisabled();

    resolveLogin?.(jsonResponse({ detail: "Invalid credentials" }, 401));

    expect(await screen.findByText("Invalid credentials")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Anmelden" })).toBeEnabled();
  });
});

it.each([true, false])("offers password recovery only with available mail (%s)", (available) => {
  render(<MemoryRouter><AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}><LoginPage mailAvailable={available} /></AuthCtx.Provider></MemoryRouter>);
  if (available) expect(screen.getByRole("link", { name: "Passwort vergessen?" })).toHaveAttribute("href", "/passwort-vergessen");
  else expect(screen.queryByRole("link", { name: "Passwort vergessen?" })).not.toBeInTheDocument();
});

it.each([true, false, undefined])("uses public metadata to show or hide recovery (%s)", async (available) => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url) => jsonResponse(url === "/api/meta"
    ? { deployment_mode: "selfhost", mail_available: available }
    : { required: false })));
  render(<MemoryRouter initialEntries={["/login"]}><AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}><AppRoutes /></AuthCtx.Provider></MemoryRouter>);
  await screen.findByRole("button", { name: "Anmelden" });
  if (available === true) expect(screen.getByRole("link", { name: "Passwort vergessen?" })).toBeInTheDocument();
  else expect(screen.queryByRole("link", { name: "Passwort vergessen?" })).not.toBeInTheDocument();
});
