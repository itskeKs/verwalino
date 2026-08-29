import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { AuthProvider } from "./AuthContext";
import { useAuth } from "./useAuth";

const session = {
  user_id: "user-1",
  organization_id: "org-1",
  role: "admin",
};

function SessionProbe() {
  const { loading, session: currentSession, login, logout } = useAuth();

  return (
    <>
      <span>{loading ? "loading" : currentSession?.role ?? "anonymous"}</span>
      <button onClick={() => login("alpha@example.test", "correct horse battery staple")}>login</button>
      <button onClick={() => logout()}>logout</button>
    </>
  );
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("AuthProvider", () => {
  it("loads the authenticated principal from the session endpoint", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(session)));

    render(
      <AuthProvider>
        <SessionProbe />
      </AuthProvider>,
    );

    await screen.findByText("admin");
    expect(fetch).toHaveBeenCalledWith(
      "/api/auth/session",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("handles an unauthenticated session probe without logging an error", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "Invalid credentials" }, 401)));

    render(
      <AuthProvider>
        <SessionProbe />
      </AuthProvider>,
    );

    await screen.findByText("anonymous");
    expect(consoleError).not.toHaveBeenCalled();
  });

  it("keeps the login response only in memory and clears it after logout", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse({ detail: "Invalid credentials" }, 401))
        .mockResolvedValueOnce(jsonResponse(session))
        .mockResolvedValueOnce(new Response(null, { status: 204 })),
    );

    render(
      <AuthProvider>
        <SessionProbe />
      </AuthProvider>,
    );

    await screen.findByText("anonymous");
    await screen.getByRole("button", { name: "login" }).click();
    await screen.findByText("admin");
    expect(sessionStorage.getItem("hv_session")).toBeNull();
    expect(fetch).toHaveBeenNthCalledWith(
      2,
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        body: JSON.stringify({
          email: "alpha@example.test",
          password: "correct horse battery staple",
        }),
      }),
    );

    await screen.getByRole("button", { name: "logout" }).click();
    await waitFor(() => expect(screen.getByText("anonymous")).toBeInTheDocument());
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/auth/logout",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
  });
});
