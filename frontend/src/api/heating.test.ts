import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { computeSettlement, finalizeSettlement, getHeatingConsumption, previewSettlement } from "./endpoints";
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());
it("encodes heating candidate identity and all selectors without touching generic suggestions", async () => {
  await getHeatingConsumption("run/id", "cost&id", "HEIZUNG & test", "HEATED_AREA");
  const url = new URL(fetchMock.mock.calls[0][0], "http://test");
  expect(url.pathname).toBe("/api/settlements/run%2Fid/heating-consumption");
  expect([...url.searchParams.entries()]).toEqual([["cost_entry_id", "cost&id"], ["meter_type", "HEIZUNG & test"], ["base_key", "HEATED_AREA"]]);
  expect(fetchMock.mock.calls[0][1].credentials).toBe("include");
});
it.each([computeSettlement, previewSettlement, finalizeSettlement])("distinguishes an omitted body from explicit empty arrays", async endpoint => {
  await endpoint("run");
  expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
  const cleared = { external_amounts: [], manual_amounts: [], unit_metrics: [], heating_inputs: [] };
  await endpoint("run", cleared);
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(cleared);
});
