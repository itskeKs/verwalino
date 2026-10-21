import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthCtx } from "../auth/useAuth";
import * as endpoints from "../api/endpoints";
import { DashboardPage } from "./DashboardPage";
import type { Task } from "../api/types";
vi.mock("../components/AccountingScopeSelector", () => ({ AccountingScopeSelector: ({ onChange }: { onChange: (id: string | null) => void }) => <select aria-label="Abrechnungskreis" onChange={e => onChange(e.target.value || null)}><option value="">Wählen</option><option value="scope">Kreis</option></select> }));
const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const task: Task = { id: "task", organization_id: "org", created_by: "user", assigned_user_id: "user", title: "Morgen erledigen", note: "", due_date: iso(tomorrow), completed: false, property_id: null, unit_id: null, lease_agreement_id: null, property_name: null, unit_number: null, lease_label: null, assigned_user_name: "Anna", created_at: "2026-10-21", updated_at: "2026-10-21" };
function show(role = "CLERK") { return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "user", organization_id: "org", role }, login: vi.fn(), logout: vi.fn() }}><MemoryRouter><DashboardPage /></MemoryRouter></AuthCtx.Provider>); }
beforeEach(() => {
  vi.spyOn(endpoints, "getMyOrganization").mockResolvedValue({ id: "org", name: "Büro" });
  vi.spyOn(endpoints, "listProperties").mockResolvedValue([]);
  vi.spyOn(endpoints, "listPersons").mockResolvedValue([]);
  vi.spyOn(endpoints, "getFristenContext").mockResolvedValue({ requires_accounting_scope: false });
  vi.spyOn(endpoints, "listFristen").mockResolvedValue([]);
  vi.spyOn(endpoints, "listTasks").mockResolvedValue({ items: [task], next_offset: null });
  vi.spyOn(endpoints, "updateTask").mockResolvedValue({ ...task, completed: true });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("shows tomorrow through seven calendar days inclusive, and removes only after server success", async () => {
  show(); expect(await screen.findByText(task.title)).toBeVisible();
  const through = new Date(); through.setDate(through.getDate() + 7);
  expect(endpoints.listTasks).toHaveBeenCalledWith(expect.objectContaining({ completed: false, due_until: iso(through) }));
  fireEvent.click(screen.getByRole("button", { name: "Erledigen" }));
  await waitFor(() => expect(endpoints.updateTask).toHaveBeenCalledWith("task", { completed: true }));
  await waitFor(() => expect(screen.queryByText(task.title)).toBeNull());
});
it("does not call unscoped financial GET when explicit scope is required, independently loads counts and tasks", async () => {
  vi.mocked(endpoints.getFristenContext).mockResolvedValue({ requires_accounting_scope: true });
  show(); await screen.findByText(task.title);
  expect(await screen.findByText("Finanzielle Fristen: Abrechnungskreis auswählen")).toBeVisible();
  expect(endpoints.listFristen).not.toHaveBeenCalled(); expect(endpoints.listProperties).toHaveBeenCalled();
  expect(screen.queryByText(/Alles erledigt/)).toBeNull();
  fireEvent.change(screen.getByLabelText("Abrechnungskreis"), { target: { value: "scope" } });
  await waitFor(() => expect(endpoints.listFristen).toHaveBeenCalledWith(expect.any(String), "scope"));
});
it("retains authorized legacy unscoped financial overview", async () => {
  show(); await waitFor(() => expect(endpoints.listFristen).toHaveBeenCalledWith(expect.any(String), undefined));
  expect(await screen.findByText(/Alles erledigt/)).toBeVisible();
});
it("does not convert context failure to false and retries it", async () => {
  vi.mocked(endpoints.getFristenContext).mockRejectedValueOnce(new Error("Kontext offline"));
  show(); expect(await screen.findByRole("alert")).toHaveTextContent("Kontext offline");
  expect(endpoints.listFristen).not.toHaveBeenCalled(); expect(await screen.findByText(task.title)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Fristen erneut laden" }));
  await waitFor(() => expect(endpoints.listFristen).toHaveBeenCalledTimes(1));
});
it.each(["READ_ONLY", "UNKNOWN"])("does not offer task completion for %s", async role => {
  show(role); await screen.findByText(task.title); expect(screen.queryByRole("button", { name: "Erledigen" })).toBeNull();
});
it("keeps failed task completion visible and permits retry", async () => {
  vi.mocked(endpoints.updateTask).mockRejectedValueOnce(new Error("offline"));
  show(); await screen.findByText(task.title); fireEvent.click(screen.getByRole("button", { name: "Erledigen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("offline"); expect(screen.getByText(task.title)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Erledigen" }));
  await waitFor(() => expect(screen.queryByText(task.title)).toBeNull());
});
it("ignores an old unscoped legacy response after scope selection", async () => {
  let resolve!: (rows: Awaited<ReturnType<typeof endpoints.listFristen>>) => void;
  vi.mocked(endpoints.listFristen).mockReturnValueOnce(new Promise(r => { resolve = r; }));
  show(); await waitFor(() => expect(endpoints.listFristen).toHaveBeenCalledTimes(1));
  fireEvent.change(screen.getByLabelText("Abrechnungskreis"), { target: { value: "scope" } });
  await waitFor(() => expect(endpoints.listFristen).toHaveBeenCalledTimes(2));
  resolve([{ kind: "OFFENE_POSTEN", status: "OVERDUE", due_date: null, reference: "Private alte Zeile", detail: "12", property_id: "p", is_statutory: true, is_provisional: false }]);
  await waitFor(() => expect(screen.queryByText("Private alte Zeile")).toBeNull());
});

it("does not display undated, completed or later tasks as due", async () => {
  const later = new Date(); later.setDate(later.getDate() + 8);
  vi.mocked(endpoints.listTasks).mockResolvedValue({ items: [task,
    { ...task, id: "no-date", title: "Ohne Datum", due_date: null },
    { ...task, id: "done", title: "Schon erledigt", completed: true },
    { ...task, id: "later", title: "Später", due_date: iso(later) },
  ], next_offset: null });
  show(); await screen.findByText(task.title);
  expect(screen.queryByText("Ohne Datum")).toBeNull(); expect(screen.queryByText("Schon erledigt")).toBeNull(); expect(screen.queryByText("Später")).toBeNull();
});
it("never treats malformed deadline context as legacy false", async () => {
  vi.mocked(endpoints.getFristenContext).mockResolvedValue({} as never);
  show(); expect(await screen.findByRole("alert")).toHaveTextContent("Fristenkontext nicht verfügbar");
  expect(endpoints.listFristen).not.toHaveBeenCalled();
});
it("exposes due tasks as a separately named dashboard region", async () => {
  show();
  expect(await screen.findByRole("region", { name: "Fällige Aufgaben" })).toBeVisible();
  expect(await screen.findByText(task.title)).toBeVisible();
});
