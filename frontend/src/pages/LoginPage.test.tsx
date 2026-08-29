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
