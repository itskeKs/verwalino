import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import { api, ApiError } from "../api/client";
import { UsersPage } from "./UsersPage";
import type { User } from "../api/types";

vi.mock("../api/client", async (original) => ({ ...await original<typeof import("../api/client")>(), api: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }));
let rows: (User & { role?: string | null })[];
const usersCalls = () => vi.mocked(api.get).mock.calls.filter(([path]) => path.startsWith("/api/users/page?"));
const reloads = () => usersCalls().length;
function show(role = "OWNER_ADMIN", userId = "actor") {
  const content = (nextRole: string, nextUserId: string) => <AuthCtx.Provider value={{ loading: false, session: { role: nextRole, user_id: nextUserId, organization_id: "org" }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><UsersPage /></MemoryRouter></AuthCtx.Provider>;
  const view = render(content(role, userId));
  return { ...view, changeSession: (nextRole: string, nextUserId = userId) => view.rerender(content(nextRole, nextUserId)) };
}
async function open(name = "Benutzer bearbeiten") {
  await userEvent.click(await screen.findByRole("button", { name }));
  return screen.findByRole("dialog", { name });
}
beforeEach(() => {
  vi.resetAllMocks();
  rows = [{ id: "target/id", display_name: "Eva Beispiel", email: "eva@example.test", is_active: true, role: "CLERK" }];
  vi.mocked(api.get).mockImplementation(async path => {
    if (path === "/api/organizations/me") return { id: "org", name: "Testverwaltung", import_available: false, deletion: null } as never;
    if (path.startsWith("/api/users/page?")) return { items: rows, next_cursor: null } as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  vi.mocked(api.patch).mockResolvedValue({});
  vi.mocked(api.post).mockResolvedValue(undefined);
});
afterEach(cleanup);

it("loads only the existing page and sends changed role only, then reloads", async () => {
  show(); const dialog = await open();
  expect(within(dialog).getByText(/Eva Beispiel.*eva@example.test/)).toBeVisible();
  expect(usersCalls()).toEqual([["/api/users/page?q=", expect.anything()]]);
  expect(vi.mocked(api.get).mock.calls.filter(([path]) => path === "/api/organizations/me")).toEqual([["/api/organizations/me"]]);
  await userEvent.selectOptions(within(dialog).getByLabelText("Rolle"), "READ_ONLY");
  await userEvent.click(within(dialog).getByRole("button", { name: "Änderungen speichern" }));
  expect(api.patch).toHaveBeenCalledExactlyOnceWith("/api/users/target%2Fid", { role: "READ_ONLY" });
  await waitFor(() => expect(reloads()).toBe(2));
});
it("does not send unchanged or missing roles", async () => {
  rows[0].role = undefined; show(); const dialog = await open();
  expect(within(dialog).getByLabelText("Rolle")).toHaveValue("");
  expect(within(dialog).getByRole("button", { name: "Änderungen speichern" })).toBeDisabled();
  await userEvent.click(within(dialog).getByLabelText("Aktiv"));
  await userEvent.click(within(dialog).getByLabelText(/Deaktivierung bestätigen/));
  await userEvent.click(within(dialog).getByRole("button", { name: "Änderungen speichern" }));
  expect(api.patch).toHaveBeenCalledExactlyOnceWith("/api/users/target%2Fid", { is_active: false });
});
it("requires explicit deactivation confirmation and allows reactivation", async () => {
  const view = show(); let dialog = await open();
  await userEvent.click(within(dialog).getByLabelText("Aktiv"));
  expect(within(dialog).getByRole("button", { name: "Änderungen speichern" })).toBeDisabled();
  await userEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
  expect(api.patch).not.toHaveBeenCalled();
  view.unmount(); rows[0].is_active = false; show(); dialog = await open();
  await userEvent.click(within(dialog).getByLabelText("Aktiv"));
  await userEvent.click(within(dialog).getByRole("button", { name: "Änderungen speichern" }));
  expect(api.patch).toHaveBeenCalledWith("/api/users/target%2Fid", { is_active: true });
});
it("guards self-demotion and self-deactivation with a server-conflict explanation", async () => {
  show("OWNER_ADMIN", "target/id"); const dialog = await open();
  expect(within(dialog).getByLabelText("Rolle")).toBeDisabled();
  expect(within(dialog).getByLabelText("Aktiv")).toBeDisabled();
  expect(within(dialog).getByText(/409/)).toBeVisible();
});
it.each([403, 404, 409, 422])("shows PATCH %s unchanged without retry", async status => {
  vi.mocked(api.patch).mockRejectedValueOnce(new ApiError(status, `Originalfehler ${status}`));
  show(); const dialog = await open(); await userEvent.selectOptions(within(dialog).getByLabelText("Rolle"), "OWNER_ADMIN");
  await userEvent.click(within(dialog).getByRole("button", { name: "Änderungen speichern" }));
  expect(await within(dialog).findByText(`Originalfehler ${status}`, { exact: true })).toBeVisible();
  expect(api.patch).toHaveBeenCalledTimes(1); expect(reloads()).toBe(1);
});
it("uses a blank transient password, validates 12..128, cancels and clears after success", async () => {
  show(); let dialog = await open("Passwort zurücksetzen");
  expect(within(dialog).getByText(/Eva Beispiel.*eva@example.test/)).toBeVisible();
  const input = within(dialog).getByLabelText("Neues Passwort");
  expect(input).toHaveValue(""); expect(input).toHaveAttribute("type", "password");
  for (const value of ["short", "x".repeat(129)]) {
    fireEvent.change(input, { target: { value } });
    expect(within(dialog).getByRole("button", { name: "Passwort zurücksetzen" })).toBeDisabled();
  }
  fireEvent.change(input, { target: { value: "Transient123!" } });
  await userEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
  expect(api.post).not.toHaveBeenCalled(); dialog = await open("Passwort zurücksetzen");
  expect(within(dialog).getByLabelText("Neues Passwort")).toHaveValue("");
  await userEvent.type(within(dialog).getByLabelText("Neues Passwort"), "Transient123!");
  await userEvent.click(within(dialog).getByRole("button", { name: "Passwort zurücksetzen" }));
  expect(api.post).toHaveBeenCalledExactlyOnceWith("/api/users/target%2Fid/reset-password", { password: "Transient123!" });
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  dialog = await open("Passwort zurücksetzen"); expect(within(dialog).getByLabelText("Neues Passwort")).toHaveValue("");
});
it.each([403, 404, 409, 422])("shows reset %s unchanged without retry", async status => {
  vi.mocked(api.post).mockRejectedValueOnce(new ApiError(status, `Resetfehler ${status}`));
  show(); const dialog = await open("Passwort zurücksetzen");
  await userEvent.type(within(dialog).getByLabelText("Neues Passwort"), "Transient123!");
  await userEvent.click(within(dialog).getByRole("button", { name: "Passwort zurücksetzen" }));
  expect(await within(dialog).findByText(`Resetfehler ${status}`, { exact: true })).toBeVisible();
  expect(api.post).toHaveBeenCalledTimes(1); expect(reloads()).toBe(1);
});
it("locks pending actions and ignores completion after session replacement", async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api.patch).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = show(); const dialog = await open();
  await userEvent.selectOptions(within(dialog).getByLabelText("Rolle"), "READ_ONLY");
  const submit = within(dialog).getByRole("button", { name: "Änderungen speichern" });
  fireEvent.click(submit); fireEvent.click(submit);
  expect(api.patch).toHaveBeenCalledTimes(1); expect(submit).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  view.changeSession("OWNER_ADMIN", "new-actor");
  await act(async () => resolve({}));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(reloads()).toBe(2);
});
it("does not request users or expose actions to READ_ONLY", () => {
  show("READ_ONLY"); expect(api.get).not.toHaveBeenCalled();
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
it("ignores reset completion after unmount", async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api.post).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  const view = show(); const dialog = await open("Passwort zurücksetzen");
  await userEvent.type(within(dialog).getByLabelText("Neues Passwort"), "Transient123!");
  await userEvent.click(within(dialog).getByRole("button", { name: "Passwort zurücksetzen" }));
  view.unmount(); await act(async () => resolve(undefined)); expect(reloads()).toBe(1);
});

it("creates a user once, clears the transient password and reloads the paged list", async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api.post).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  show(); await screen.findByText("eva@example.test");
  await userEvent.type(screen.getByPlaceholderText("E-Mail *"), "new@example.test");
  await userEvent.type(screen.getByPlaceholderText("Startpasswort *"), "Transient123!");
  const submit = screen.getByRole("button", { name: "Benutzer anlegen" });
  fireEvent.click(submit); fireEvent.click(submit);
  expect(api.post).toHaveBeenCalledExactlyOnceWith("/api/users", { email: "new@example.test", password: "Transient123!", display_name: "", role: "CLERK" });
  expect(submit).toBeDisabled();
  await act(async () => resolve({}));
  await waitFor(() => expect(reloads()).toBe(2));
  expect(screen.getByPlaceholderText("Startpasswort *")).toHaveValue("");
});
it("allows self password reset with an explicit session invalidation explanation", async () => {
  show("OWNER_ADMIN", "target/id"); const dialog = await open("Passwort zurücksetzen");
  expect(within(dialog).getByText(/erneute Anmeldung erforderlich/)).toBeVisible();
  await userEvent.type(within(dialog).getByLabelText("Neues Passwort"), "Transient123!");
  await userEvent.click(within(dialog).getByRole("button", { name: "Passwort zurücksetzen" }));
  expect(api.post).toHaveBeenCalledExactlyOnceWith("/api/users/target%2Fid/reset-password", { password: "Transient123!" });
});
it("blocks duplicate resets and cancellation while the reset request is pending", async () => {
  let resolve!: (value: unknown) => void;
  vi.mocked(api.post).mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  show(); const dialog = await open("Passwort zurücksetzen");
  await userEvent.type(within(dialog).getByLabelText("Neues Passwort"), "Transient123!");
  const submit = within(dialog).getByRole("button", { name: "Passwort zurücksetzen" });
  fireEvent.click(submit); fireEvent.click(submit);
  expect(api.post).toHaveBeenCalledTimes(1); expect(submit).toBeDisabled();
  expect(within(dialog).getByLabelText("Neues Passwort")).toBeDisabled();
  expect(within(dialog).getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Schließen" }));
  expect(dialog).toBeVisible();
  await act(async () => resolve(undefined));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});
