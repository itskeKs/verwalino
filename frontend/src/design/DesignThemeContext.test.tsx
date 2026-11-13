import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { DesignThemeProvider } from "./DesignThemeContext";
import { useDesignTheme } from "./useDesignTheme";
import { SettingsPage } from "../pages/SettingsPage";
import { api, ApiError } from "../api/client";

const auth = vi.hoisted(() => ({ session: { user_id: "one", organization_id: "org", role: "READ_ONLY" } as { user_id: string; organization_id: string; role: string } | null }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => auth }));
afterEach(() => { vi.restoreAllMocks(); auth.session = { user_id: "one", organization_id: "org", role: "READ_ONLY" }; });
function mockReads() {
  return vi.spyOn(api, "get").mockImplementation((url) =>
    url === "/api/organizations/legal-assents" ? Promise.resolve([]) : Promise.resolve({ design_theme: "klar" }));
}
function deferred<T>() { let resolve!: (v: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
function Probe() { const { theme } = useDesignTheme(); const [value, setValue] = useState(""); return <><output>{theme}</output><input aria-label="Unfinished work" value={value} onChange={e => setValue(e.target.value)} /></>; }
function View() { return <DesignThemeProvider><Probe /><SettingsPage /></DesignThemeProvider>; }

it("defaults to Klar and saves a read-only user's personal choice without remounting work", async () => {
  mockReads();
  const reply = deferred<{ design_theme: string }>();
  const save = vi.spyOn(api, "patch").mockReturnValue(reply.promise);
  render(<View />);
  await screen.findByRole("radio", { name: /Klar/ });
  await waitFor(() => expect(screen.getByRole("button", { name: "Design speichern" })).toBeDisabled());
  fireEvent.change(screen.getByLabelText("Unfinished work"), { target: { value: "Keep this" } });
  fireEvent.click(screen.getByRole("radio", { name: /Warm/ }));
  expect(screen.getByText("Noch nicht gespeichert.")).toBeVisible();
  expect(screen.getByText("klar", { selector: "output" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Design speichern" }));
  expect(screen.getByRole("button", { name: "Wird gespeichert …" })).toBeDisabled();
  expect(save).toHaveBeenCalledWith("/api/auth/preferences", { design_theme: "warm" }, expect.any(Object));
  await act(async () => reply.resolve({ design_theme: "warm" }));
  expect(screen.getByText("warm", { selector: "output" })).toBeVisible();
  expect(screen.getByLabelText("Unfinished work")).toHaveValue("Keep this");
  expect(screen.getByText("Design für Ihr Benutzerkonto gespeichert.")).toBeVisible();
});

it("retains the selected choice and active saved theme on a failed save", async () => {
  mockReads();
  vi.spyOn(api, "patch").mockRejectedValue(new ApiError(503, "Speichern derzeit nicht möglich."));
  render(<View />);
  await waitFor(() => expect(screen.getByRole("radio", { name: /Warm/ })).toBeEnabled());
  fireEvent.click(screen.getByRole("radio", { name: /Warm/ }));
  fireEvent.click(screen.getByRole("button", { name: "Design speichern" }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Speichern derzeit nicht möglich.");
  expect(alert.closest(".theme-save-row")).not.toBeNull();
  expect(screen.getByRole("radio", { name: /Warm/ })).toBeChecked();
  expect(screen.getByText("klar", { selector: "output" })).toBeVisible();
  expect(screen.getByRole("button", { name: "Design speichern" })).toBeEnabled();
});

it("discards a late preference read after switching user", async () => {
  const old = deferred<{ design_theme: string }>();
  mockReads().mockReturnValueOnce(old.promise);
  const view = render(<View />);
  auth.session = { user_id: "two", organization_id: "org", role: "READ_ONLY" };
  view.rerender(<View />);
  await waitFor(() => expect(screen.getByRole("radio", { name: /Warm/ })).toBeEnabled());
  await act(async () => old.resolve({ design_theme: "warm" }));
  expect(screen.getByText("klar", { selector: "output" })).toBeVisible();
  expect(screen.getByRole("radio", { name: /Klar/ })).toBeChecked();
});

it("discards a late save after logout and a different user's login", async () => {
  mockReads();
  const old = deferred<{ design_theme: string }>();
  vi.spyOn(api, "patch").mockReturnValue(old.promise);
  const view = render(<View />);
  await waitFor(() => expect(screen.getByRole("radio", { name: /Warm/ })).toBeEnabled());
  fireEvent.click(screen.getByRole("radio", { name: /Warm/ }));
  fireEvent.click(screen.getByRole("button", { name: "Design speichern" }));
  auth.session = null; view.rerender(<View />);
  auth.session = { user_id: "two", organization_id: "other", role: "OWNER_ADMIN" }; view.rerender(<View />);
  await waitFor(() => expect(screen.getByRole("radio", { name: /Warm/ })).toBeEnabled());
  await act(async () => old.resolve({ design_theme: "warm" }));
  expect(screen.getByText("klar", { selector: "output" })).toBeVisible();
  expect(screen.queryByText("Design für Ihr Benutzerkonto gespeichert.")).not.toBeInTheDocument();
});

it("reports a failed preference read honestly and offers retry before saving", async () => {
  const load = vi.spyOn(api, "get").mockRejectedValueOnce(new Error("offline")).mockImplementation((url) =>
    url === "/api/organizations/legal-assents" ? Promise.resolve([]) : Promise.resolve({ design_theme: "warm" }));
  render(<View />);
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Design konnte nicht geladen werden.");
  expect(alert.closest(".theme-save-row")).toBeNull();
  expect(screen.getByRole("button", { name: "Design speichern" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));
  await screen.findByText("warm", { selector: "output" });
  expect(load).toHaveBeenCalledTimes(2);
});
