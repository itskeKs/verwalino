import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SetupPage } from "./SetupPage";
import { AppRoutes } from "../App";
import { AuthCtx } from "../auth/useAuth";

const complete = vi.fn();
const token = "12".repeat(32);
const password = "A-long-setup-password-123";

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function open() {
  return render(<MemoryRouter initialEntries={["/setup"]}><Routes>
    <Route path="/setup" element={<SetupPage onComplete={complete} />} />
    <Route path="/login" element={<p>Anmeldung</p>} />
  </Routes></MemoryRouter>);
}

async function fill(repeat = password) {
  await screen.findByRole("heading", { name: "Verwalino einrichten" });
  for (const [label, value] of [
    ["Organisation", "Hausverwaltung mit einem sehr langen deutschen Organisationsnamen GmbH"],
    ["Name", "Erika Mustermann"], ["E-Mail", "erika@example.test"], ["Passwort", password],
    ["Passwort wiederholen", repeat], ["Einrichtungs-Token", token],
  ]) {
    fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } });
  }
}

beforeEach(() => {
  complete.mockReset();
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => json({ required: true })));
});

describe("SetupPage", () => {
  it("routes an unclaimed installation from app load to setup", async () => {
    render(<AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}>
      <MemoryRouter><AppRoutes /></MemoryRouter>
    </AuthCtx.Provider>);
    expect(await screen.findByRole("heading", { name: "Verwalino einrichten" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Anmelden" })).not.toBeInTheDocument();
  });

  it("routes a closed setup URL to the login page on app load", async () => {
    vi.mocked(fetch).mockResolvedValue(json({ required: false }));
    render(<AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}>
      <MemoryRouter initialEntries={["/setup"]}><AppRoutes /></MemoryRouter>
    </AuthCtx.Provider>);
    expect(await screen.findByRole("button", { name: "Anmelden" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Einrichtungs-Token")).not.toBeInTheDocument();
  });

  it("leaves setup through AppRoutes when another operator completes it", async () => {
    let checks = 0;
    vi.mocked(fetch).mockImplementation(async (_url, options) => {
      if (options?.method === "POST") return json({ detail: "Not Found" }, 404);
      checks += 1;
      if (checks > 5) throw new Error("Repeated setup redirects");
      return json({ required: checks <= 2 });
    });
    render(<AuthCtx.Provider value={{ loading: false, session: null, login: vi.fn(), logout: vi.fn() }}>
      <MemoryRouter><AppRoutes /></MemoryRouter>
    </AuthCtx.Provider>);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Einrichtung abschließen" }));
    expect(await screen.findByRole("button", { name: "Anmelden" })).toBeInTheDocument();
    expect(checks).toBeLessThanOrEqual(3);
  });

  it("submits the token only in the body and continues with the authenticated session", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ required: true })).mockResolvedValueOnce(json({ role: "OWNER_ADMIN" }, 201));
    open();
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Einrichtung abschließen" }));
    await waitFor(() => expect(complete).toHaveBeenCalledOnce());
    const [url, options] = vi.mocked(fetch).mock.calls[1];
    expect(url).toBe("/api/setup");
    expect(options).toMatchObject({ method: "POST", credentials: "include" });
    expect(JSON.parse(options!.body as string)).toEqual({
      organization_name: "Hausverwaltung mit einem sehr langen deutschen Organisationsnamen GmbH",
      admin_name: "Erika Mustermann", admin_email: "erika@example.test", password, token,
    });
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });

  it("keeps the form and useful inputs when a token is refused", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ required: true })).mockResolvedValueOnce(json({ detail: "Einrichtung nicht möglich. Bitte prüfen Sie den Einrichtungs-Token." }, 403));
    open(); await fill();
    await userEvent.click(screen.getByRole("button", { name: "Einrichtung abschließen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Einrichtungs-Token");
    expect(screen.getByLabelText("Name", { exact: true })).toHaveValue("Erika Mustermann");
    expect(complete).not.toHaveBeenCalled();
  });

  it("explains a password mismatch before submitting", async () => {
    open(); await fill("Another-password-123");
    await userEvent.click(screen.getByRole("button", { name: "Einrichtung abschließen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Passwörter stimmen nicht überein");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("redirects to login once setup is no longer required", async () => {
    vi.mocked(fetch).mockResolvedValue(json({ required: false }));
    open();
    expect(await screen.findByText("Anmeldung")).toBeInTheDocument();
    expect(screen.queryByLabelText("Einrichtungs-Token")).not.toBeInTheDocument();
  });

  it("redirects if another request completes setup while the form is open", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json({ required: true })).mockResolvedValueOnce(json({ detail: "Not Found" }, 404));
    open(); await fill();
    await userEvent.click(screen.getByRole("button", { name: "Einrichtung abschließen" }));
    expect(await screen.findByText("Anmeldung")).toBeInTheDocument();
  });

  it("reports a status failure and allows retry without showing the form", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("network"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Einrichtungsstatus");
    expect(screen.queryByLabelText("Einrichtungs-Token")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Erneut versuchen" }));
    expect(await screen.findByLabelText("Einrichtungs-Token")).toBeInTheDocument();
  });
});
