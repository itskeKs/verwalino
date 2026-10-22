import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { getDashboardKpis } from "./dashboardKpis";

beforeEach(() => vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}"))));
afterEach(() => vi.unstubAllGlobals());
it("GETs only encoded contract fields with credentials and the abort signal", async () => {
  const controller = new AbortController();
  await getDashboardKpis({ property_id: "p&/", year: 2026, month: 10, accounting_scope_id: "s&/", organization_id: "ignore" } as Parameters<typeof getDashboardKpis>[0], controller.signal);
  expect(fetch).toHaveBeenCalledWith("/api/reports/dashboard-kpis?property_id=p%26%2F&year=2026&month=10&accounting_scope_id=s%26%2F", expect.objectContaining({ credentials: "include", signal: controller.signal }));
  expect(vi.mocked(fetch).mock.calls[0][1]?.body).toBeUndefined();
});
it("omits scope for authorized legacy access and permits boundary dates", async () => {
  await getDashboardKpis({ property_id: "p", year: 1, month: 1 });
  expect(fetch).toHaveBeenCalledWith("/api/reports/dashboard-kpis?property_id=p&year=1&month=1", expect.anything());
});
it.each([{ year: 0, month: 1 }, { year: 9999, month: 1 }, { year: 2026, month: 0 }, { year: 2026, month: 13 }, { year: 2026.5, month: 1 }, { year: 2026, month: 1.5 }])("rejects invalid date fields before fetching: %j", async fields => {
  await expect(getDashboardKpis({ property_id: "p", ...fields })).rejects.toThrow();
  expect(fetch).not.toHaveBeenCalled();
});
it.each([403, 409, 422])("preserves HTTP %s and detail", async status => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: "Historischer Zugriff fehlt" }), { status }));
  await expect(getDashboardKpis({ property_id: "p", year: 2026, month: 10 })).rejects.toMatchObject({ status, message: "Historischer Zugriff fehlt" });
});
