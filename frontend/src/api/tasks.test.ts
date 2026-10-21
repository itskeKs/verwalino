import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTask, deleteTask, getTask, getFristenContext, listTaskAssignees, listTasks, updateTask } from "./endpoints";
import { ApiError } from "./client";
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], next_offset: null }), { status: 200 })); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());
it("whitelists AND filters and encodes query values including completed false", async () => {
  await listTasks({ property_id: "p&x", unit_id: "u/1", lease_agreement_id: "l", assigned_user_id: "a", completed: false, due_until: "2026-10-28", offset: 50, limit: 100, organization_id: "spoof" } as Parameters<typeof listTasks>[0]);
  const url = new URL(fetchMock.mock.calls[0][0], "http://test");
  expect(url.pathname).toBe("/api/tasks");
  expect(Object.fromEntries(url.searchParams)).toEqual({ property_id: "p&x", unit_id: "u/1", lease_agreement_id: "l", assigned_user_id: "a", completed: "false", due_until: "2026-10-28", offset: "50", limit: "100" });
  expect(fetchMock.mock.calls[0][1].credentials).toBe("include");
});
it("whitelists mutations, preserves explicit null clearing and encodes task IDs", async () => {
  await createTask({ title: "Neu", note: "Text", due_date: null, organization_id: "spoof", completed: false } as Parameters<typeof createTask>[0]);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ title: "Neu", note: "Text", due_date: null, completed: false });
  await updateTask("t/id", { completed: false, due_date: null, property_id: null, unit_id: null, lease_agreement_id: null, created_by: "spoof" } as Parameters<typeof updateTask>[1]);
  expect(fetchMock.mock.calls[1][0]).toBe("/api/tasks/t%2Fid");
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ completed: false, due_date: null, property_id: null, unit_id: null, lease_agreement_id: null });
  await getTask("t/id"); expect(fetchMock.mock.calls[2][0]).toBe("/api/tasks/t%2Fid");
  fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
  await deleteTask("t/id"); expect(fetchMock.mock.calls[3][1].method).toBe("DELETE");
});
it("reads deadline context without fetching financial rows", async () => {
  await getFristenContext(); expect(fetchMock).toHaveBeenCalledTimes(1); expect(fetchMock.mock.calls[0][0]).toBe("/api/fristen/context");
});
it.each([403, 404, 409, 422])("preserves ApiError %s", async status => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "denied" }), { status }));
  await expect(listTasks()).rejects.toBeInstanceOf(ApiError);
  await expect(updateTask("t", { completed: true })).rejects.toMatchObject({ status });
});
it("preserves network errors", async () => { fetchMock.mockRejectedValue(new TypeError("offline")); await expect(listTasks()).rejects.toThrow("offline"); });

it("reads active assignees through the existing cursor-paginated organization directory", async () => {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: "a", display_name: "Anna", email: "anna@example.test", is_active: true }], next_cursor: "cursor&next" })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [{ id: "b", display_name: "Berta", email: "berta@example.test", is_active: true }], next_cursor: null })));
  const members = await listTaskAssignees();
  expect(members.map(member => member.display_name)).toEqual(["Anna", "Berta"]);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/users/page?eligible_for_scope=true");
  const next = new URL(fetchMock.mock.calls[1][0], "http://test");
  expect(next.searchParams.get("cursor")).toBe("cursor&next"); expect(next.searchParams.get("eligible_for_scope")).toBe("true");
});
