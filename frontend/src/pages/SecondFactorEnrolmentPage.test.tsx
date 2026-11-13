import { MemoryRouter } from "react-router-dom";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { AuthCtx } from "../auth/useAuth";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

it("sends a restricted session back to the enrolment wizard after a reload", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (url: RequestInfo | URL) => {
    const path = typeof url === "string" ? url : url instanceof URL ? url.pathname : url.url;
    if (path === "/api/meta") return jsonResponse({ deployment_mode: "selfhost", version: "0.4.0", mail_available: false });
    if (path === "/api/setup") return jsonResponse({ required: false });
    return jsonResponse({ detail: "Not authenticated" }, 401);
  }));
  const session = {
    user_id: "u1",
    organization_id: "o1",
    role: "OWNER_ADMIN",
    second_factor_enrolment_required: true,
  };
  render(
    <MemoryRouter initialEntries={["/objekte"]}>
      <AuthCtx.Provider value={{ loading: false, session, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout: vi.fn() }}>
        <AppRoutes />
      </AuthCtx.Provider>
    </MemoryRouter>,
  );
  // A browser reload on a deep route lands on the wizard, not on a 403-ing app.
  expect(await screen.findByRole("heading", { name: "Zweiten Faktor einrichten" })).toBeInTheDocument();
});

it("lets a restricted session sign out from the enrolment wizard", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "Not authenticated" }, 401)));
  const logout = vi.fn().mockResolvedValue(undefined);
  const session = { user_id: "u1", organization_id: "o1", role: "OWNER_ADMIN", second_factor_enrolment_required: true };
  render(
    <MemoryRouter initialEntries={["/zweiter-faktor"]}>
      <AuthCtx.Provider value={{ loading: false, session, login: vi.fn(), completeSecondFactor: vi.fn(), reloadSession: vi.fn(), logout }}>
        <AppRoutes />
      </AuthCtx.Provider>
    </MemoryRouter>,
  );
  await userEvent.click(await screen.findByRole("button", { name: "Abmelden" }));
  expect(logout).toHaveBeenCalledTimes(1);
});
