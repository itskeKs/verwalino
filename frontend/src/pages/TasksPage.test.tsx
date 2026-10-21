import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import { TasksPage } from "./TasksPage";
import type { Task } from "../api/types";

const task: Task = { id: "task-1", organization_id: "org", created_by: "user", assigned_user_id: "user", property_id: null, unit_id: null, lease_agreement_id: null, title: "Heizung prüfen", note: "<b>Nur Text</b>", due_date: "2026-10-22", completed: false, created_at: "2026-10-21", updated_at: "2026-10-21", property_name: null, unit_number: null, lease_label: null, assigned_user_name: "Anna" };
function show(role = "CLERK", path = "/aufgaben") {
  return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={[path]}><TasksPage /></MemoryRouter></AuthCtx.Provider>);
}
beforeEach(() => {
  vi.spyOn(endpoints, "listTasks").mockResolvedValue({ items: [task], next_offset: null });
  vi.spyOn(endpoints, "listProperties").mockResolvedValue([]);
  vi.spyOn(endpoints, "listUnits").mockResolvedValue([]);
  vi.spyOn(endpoints, "listLeases").mockResolvedValue([]);
  vi.spyOn(endpoints, "listTaskAssignees").mockResolvedValue([{ id: "user", email: "anna@example.test", display_name: "Anna", is_active: true }]);
  vi.spyOn(endpoints, "createTask").mockResolvedValue(task);
  vi.spyOn(endpoints, "updateTask").mockResolvedValue({ ...task, completed: true });
  vi.spyOn(endpoints, "deleteTask").mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("creates a global undated task without an invented object or accounting scope", async () => {
  show(); await screen.findByText(task.title);
  fireEvent.click(screen.getByRole("button", { name: "Aufgabe anlegen" }));
  fireEvent.change(screen.getByLabelText("Titel *"), { target: { value: "  Rückruf  " } });
  fireEvent.change(screen.getByLabelText("Notiz"), { target: { value: "<script>plain</script>" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(endpoints.createTask).toHaveBeenCalledWith({ title: "Rückruf", note: "<script>plain</script>", due_date: null, assigned_user_id: "user", property_id: null, unit_id: null, lease_agreement_id: null }));
});
it.each(["READ_ONLY", "UNKNOWN"])("does not offer writes for %s", async role => {
  show(role); await screen.findByText(task.title);
  expect(screen.queryByRole("button", { name: "Aufgabe anlegen" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Erledigen" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Bearbeiten" })).toBeNull();
});
it.each([403, 404, 409, 422])("keeps edit contents after API %s and permits retry", async status => {
  vi.mocked(endpoints.updateTask).mockRejectedValueOnce(new ApiError(status, "Nicht gespeichert"));
  show(); await screen.findByText(task.title); fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Titel *"), { target: { value: "Neue Notiz" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Nicht gespeichert");
  expect(screen.getByLabelText("Titel *")).toHaveValue("Neue Notiz");
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(endpoints.updateTask).toHaveBeenCalledTimes(2));
});
it("locks mutations synchronously and cannot cancel while saving", async () => {
  let finish!: (task: Task) => void;
  vi.mocked(endpoints.createTask).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  show(); await screen.findByText(task.title); fireEvent.click(screen.getByRole("button", { name: "Aufgabe anlegen" }));
  fireEvent.change(screen.getByLabelText("Titel *"), { target: { value: "Neu" } });
  const save = screen.getByRole("button", { name: "Speichern" });
  fireEvent.click(save); fireEvent.click(save);
  expect(endpoints.createTask).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Titel *")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  finish(task); await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});
it("renders notes as plaintext, completes, reopens and confirms deletion", async () => {
  show(); await screen.findByText(task.title); expect(screen.getByText(task.note)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Erledigen" }));
  await waitFor(() => expect(endpoints.updateTask).toHaveBeenCalledWith(task.id, { completed: true }));
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "completed" } });
  vi.mocked(endpoints.listTasks).mockResolvedValue({ items: [{ ...task, completed: true }], next_offset: null });
  fireEvent.click(await screen.findByRole("button", { name: "Erneut laden" }));
  fireEvent.click(await screen.findByRole("button", { name: "Wiederöffnen" }));
  await waitFor(() => expect(endpoints.updateTask).toHaveBeenCalledWith(task.id, { completed: false }));
  fireEvent.click(screen.getByRole("button", { name: "Löschen" }));
  const dialog = screen.getByRole("dialog", { name: "Aufgabe löschen?" });
  expect(endpoints.deleteTask).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Aufgabe löschen" }));
  await waitFor(() => expect(endpoints.deleteTask).toHaveBeenCalledWith(task.id));
});
it("does not post on a context create link and refuses an unobserved parent", async () => {
  show("CLERK", "/aufgaben?property_id=foreign&unit_id=foreign-unit&create=1");
  expect(await screen.findByRole("alert")).toHaveTextContent(/Kontext/);
  expect(endpoints.createTask).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Speichern" })).toBeNull();
});

it("opens observed property/unit context with human names and no POST until explicit save", async () => {
  vi.mocked(endpoints.listProperties).mockResolvedValue([{ id: "p", name: "Gartenhaus" }] as never);
  vi.mocked(endpoints.listUnits).mockResolvedValue([{ id: "u", property_id: "p", number: "42", location: "Dachgeschoss" }] as never);
  show("CLERK", "/aufgaben?property_id=p&unit_id=u&create=1");
  const dialog = await screen.findByRole("dialog", { name: "Aufgabe anlegen" });
  expect(within(dialog).getByText(/Fester Kontext: Gartenhaus · Einheit 42/)).toBeVisible();
  expect(screen.getByLabelText("Objekt")).toHaveValue("p"); expect(screen.getByLabelText("Objekt")).toBeDisabled();
  expect(screen.getByLabelText("Einheit")).toHaveValue("u"); expect(screen.getByLabelText("Einheit")).toBeDisabled();
  expect(endpoints.createTask).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Titel *"), { target: { value: "Kontrolle" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(endpoints.createTask).toHaveBeenCalledWith(expect.objectContaining({ property_id: "p", unit_id: "u", lease_agreement_id: null })));
});
it("ignores obsolete status replies and paginates using the server offset", async () => {
  let finish!: (page: { items: Task[]; next_offset: number | null }) => void;
  vi.mocked(endpoints.listTasks).mockReturnValueOnce(new Promise(resolve => { finish = resolve; })).mockResolvedValue({ items: [{ ...task, title: "Erledigte Kontrolle", completed: true }], next_offset: 50 });
  show(); await waitFor(() => expect(endpoints.listTasks).toHaveBeenCalledOnce());
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "completed" } });
  await screen.findByText("Erledigte Kontrolle");
  await act(async () => finish({ items: [{ ...task, title: "Private alte Antwort" }], next_offset: null }));
  expect(screen.queryByText("Private alte Antwort")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Weiter" }));
  await waitFor(() => expect(endpoints.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ completed: true, offset: 50, limit: 50 })));
  fireEvent.click(screen.getByRole("button", { name: "Zurück" }));
  await waitFor(() => expect(endpoints.listTasks).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 0 })));
});
it("retains rows on refresh failure, displays the actual network error and retries", async () => {
  show(); await screen.findByText(task.title);
  vi.mocked(endpoints.listTasks).mockRejectedValueOnce(new TypeError("Netzwerk offline"));
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Netzwerk offline");
  expect(screen.getByText(task.title)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
});
it("keeps an old inactive assignee while clearing the due date and all parents", async () => {
  vi.mocked(endpoints.listTasks).mockResolvedValue({ items: [{ ...task, assigned_user_id: "inactive", assigned_user_name: "Eva", property_id: "p", unit_id: "u", lease_agreement_id: "l" }], next_offset: null });
  vi.mocked(endpoints.listProperties).mockResolvedValue([{ id: "p", name: "Haus" }] as never);
  vi.mocked(endpoints.listUnits).mockResolvedValue([{ id: "u", property_id: "p", number: "1" }] as never);
  vi.mocked(endpoints.listLeases).mockResolvedValue([{ id: "l", unit_id: "u", valid_from: "2025-01-01", valid_to: null }] as never);
  show(); await screen.findByText(task.title); await waitFor(() => expect(screen.getByRole("button", { name: "Bearbeiten" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Bearbeiten" }));
  expect(screen.getByRole("option", { name: "Eva (bisherige Zuordnung)" })).toBeVisible();
  fireEvent.change(screen.getByLabelText("Fällig am"), { target: { value: "" } });
  fireEvent.change(screen.getByLabelText("Objekt"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  await waitFor(() => expect(endpoints.updateTask).toHaveBeenCalledWith(task.id, expect.objectContaining({ due_date: null, assigned_user_id: "inactive", property_id: null, unit_id: null, lease_agreement_id: null })));
});
it("replaces private rows immediately when link context changes and ignores an old completion", async () => {
  vi.mocked(endpoints.listProperties).mockResolvedValue([{ id: "p", name: "Haus" }, { id: "other", name: "Neues Haus" }] as never);
  let finish!: (task: Task) => void;
  vi.mocked(endpoints.updateTask).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role: "CLERK" }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter initialEntries={["/aufgaben?property_id=p"]}><Link to="/aufgaben?property_id=other">Kontext wechseln</Link><TasksPage /></MemoryRouter></AuthCtx.Provider>);
  await screen.findByText(task.title); fireEvent.click(screen.getByRole("button", { name: "Erledigen" }));
  vi.mocked(endpoints.listTasks).mockResolvedValue({ items: [], next_offset: null });
  fireEvent.click(screen.getByRole("link", { name: "Kontext wechseln" }));
  expect(screen.queryByText(task.title)).toBeNull();
  await act(async () => finish({ ...task, completed: true }));
  expect(await screen.findByText("Keine Aufgaben gefunden.")).toBeVisible(); expect(screen.queryByText(task.title)).toBeNull();
});
it("keeps pending forms open for Escape and close, and retains contents after network failure", async () => {
  let fail!: (error: Error) => void;
  vi.mocked(endpoints.createTask).mockReturnValueOnce(new Promise((_resolve, reject) => { fail = reject; }));
  show(); await screen.findByText(task.title); fireEvent.click(screen.getByRole("button", { name: "Aufgabe anlegen" }));
  fireEvent.change(screen.getByLabelText("Titel *"), { target: { value: "Unverloren" } });
  fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" }); fireEvent.click(screen.getByRole("button", { name: "Schließen" }));
  expect(screen.getByRole("dialog")).toBeVisible();
  await act(async () => fail(new TypeError("offline")));
  expect(screen.getByRole("alert")).toHaveTextContent("offline"); expect(screen.getByLabelText("Titel *")).toHaveValue("Unverloren");
});
it("keeps exact native select names independent of their option text", async () => {
  show(); await screen.findByText(task.title);
  for (const label of ["Status", "Objektfilter", "Zuständigkeitsfilter"])
    expect(screen.getByLabelText(label, { selector: "select" })).toHaveAttribute("aria-label", label);
  await waitFor(() => expect(screen.getByRole("button", { name: "Aufgabe anlegen" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Aufgabe anlegen" }));
  const dialog = screen.getByRole("dialog", { name: "Aufgabe anlegen" });
  for (const label of ["Zuständig", "Objekt", "Einheit", "Mietvertrag"])
    expect(within(dialog).getByLabelText(label, { selector: "select" })).toHaveAttribute("aria-label", label);
});
