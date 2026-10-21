import { afterEach, expect, it, vi } from "vitest";
import * as endpoints from "./endpoints";
import { ApiError } from "./client";

afterEach(() => vi.unstubAllGlobals());

it("exports payments with all seven known filters, encoded, without unknown parameters", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("\ufeffID;Betrag (EUR)\r\n", { headers: { "Content-Type": "text/csv" } }));
  vi.stubGlobal("fetch", fetch);
  const query = { bank_account_id: "a &b", lease_agreement_id: "l/?", tenant_profile_id: "t#", owner_profile_id: "o=", payment_type: "KALTMIETE", period_year: 2026, status: "booked", unknown: "secret", accounting_scope_id: "ignored" };
  const blob = await endpoints.exportPaymentsCsv(query);
  expect(blob).toBeInstanceOf(Blob);
  expect(await blob.text()).toContain("ID;Betrag (EUR)\r\n");
  const [url, options] = fetch.mock.calls[0];
  expect(url.split("?")[0]).toBe("/api/payments/export.csv");
  expect(Object.fromEntries(new URLSearchParams(url.split("?")[1]))).toEqual({ bank_account_id: "a &b", lease_agreement_id: "l/?", tenant_profile_id: "t#", owner_profile_id: "o=", payment_type: "KALTMIETE", period_year: "2026", status: "booked" });
  expect(options.credentials).toBe("include");
  expect(options.method ?? "GET").toBe("GET");
});

it("exports costs with only status and property, sharing the list query and preserving bytes", async () => {
  const bytes = new Uint8Array([239, 187, 191, 73, 68, 59, 13, 10]);
  const fetch = vi.fn().mockImplementation(async (url: string) => url.includes("export.csv") ? new Response(bytes) : new Response("[]"));
  vi.stubGlobal("fetch", fetch);
  const query = { status: "confirmed", property_id: "p &?", unknown: "no", period_year: 2026 };
  const blob = await endpoints.exportCostEntriesCsv(query);
  expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
  await endpoints.listCostEntries(query);
  expect(fetch.mock.calls[0][0]).toBe("/api/cost-entries/export.csv" + fetch.mock.calls[1][0].slice("/api/cost-entries".length));
  expect(new URLSearchParams(fetch.mock.calls[0][0].split("?")[1]).get("unknown")).toBeNull();
});

it.each(["payments", "costs"])("omits empty filters for %s", async kind => {
  const fetch = vi.fn().mockResolvedValue(new Response("ID\r\n"));
  vi.stubGlobal("fetch", fetch);
  await (kind === "payments" ? endpoints.exportPaymentsCsv({ status: "", bank_account_id: "" }) : endpoints.exportCostEntriesCsv({ status: "", property_id: "" }));
  expect(fetch.mock.calls[0][0]).toBe(`/api/${kind === "payments" ? "payments" : "cost-entries"}/export.csv`);
});

it.each([401, 403, 404, 409, 422])("preserves credentialed ApiError %s without a fake blob", async status => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify({ detail: "Export verweigert" }), { status })));
  await expect(endpoints.exportPaymentsCsv()).rejects.toEqual(new ApiError(status, "Export verweigert"));
  await expect(endpoints.exportCostEntriesCsv()).rejects.toEqual(new ApiError(status, "Export verweigert"));
});
