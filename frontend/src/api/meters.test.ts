import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createMeter, createMeterReading, listMeterReadings, listMeters, updateMeter } from "./endpoints";

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(JSON.stringify([]), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());
function body() { return JSON.parse(fetchMock.mock.calls[0][1].body as string); }

it("encodes property/unit filters and keeps session credentials", async () => {
  await listMeters("property&other", "unit/one");
  const url = new URL(fetchMock.mock.calls[0][0], "http://test");
  expect(url.pathname).toBe("/api/meters");
  expect([...url.searchParams.entries()]).toEqual([["property_id", "property&other"], ["unit_id", "unit/one"]]);
  expect(fetchMock.mock.calls[0][1].credentials).toBe("include");
});
it("omits the unit filter for the complete property list", async () => {
  await listMeters("property");
  expect(fetchMock.mock.calls[0][0]).toBe("/api/meters?property_id=property");
});
it("creates a common-area meter without forwarding arbitrary identity fields", async () => {
  const input = { property_id: "property", meter_number: "W-1", meter_type: "KALTWASSER", organization_id: "spoof", id: "spoof" };
  await createMeter(input);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/meters");
  expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  expect(body()).toEqual({ property_id: "property", unit_id: null, meter_number: "W-1", meter_type: "KALTWASSER", location: "" });
});
it("patches descriptors only, never parents or organization", async () => {
  const input = { meter_number: "W-2", property_id: "wrong", unit_id: "wrong", organization_id: "wrong" };
  await updateMeter("meter/id", input);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/meters/meter%2Fid");
  expect(fetchMock.mock.calls[0][1].method).toBe("PATCH");
  expect(body()).toEqual({ meter_number: "W-2" });
});
it("loads the selected meter history with an encoded path", async () => {
  await listMeterReadings("meter/id");
  expect(fetchMock.mock.calls[0][0]).toBe("/api/meters/meter%2Fid/readings");
});
it("sends an exact reading without a second body identity", async () => {
  const input = { reading_date: "2026-12-31", value: "250.1250", period: "2026", meter_id: "wrong", organization_id: "wrong" };
  await createMeterReading("meter", input);
  expect(fetchMock.mock.calls[0][0]).toBe("/api/meters/meter/readings");
  expect(body()).toEqual({ reading_date: "2026-12-31", value: "250.1250", period: "2026" });
});
