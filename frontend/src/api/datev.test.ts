import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { downloadDatevCsv, getDatevSources, previewDatev, type DatevConfig } from "./datev";

const config: DatevConfig = {
  property_id: "p 1", accounting_scope_id: null, period_start: "2026-01-01", period_end: "2026-12-31",
  fiscal_year_start: "2026-01-01", chart: "SKR04", advisor_number: "00123", client_number: "00456", account_length: 4,
  bank_accounts: { b1: "1234" }, payment_accounts: { KALTMIETE: "5678" }, category_accounts: { c1: { account: "2345", counter_account: "6789" } }, gross_accounts_reviewed: true,
};
afterEach(() => vi.unstubAllGlobals());
describe("DATEV API", () => {
  it("encodes source selection and omits an absent scope", async () => {
    const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ banks: [], categories: [], payment_types: [], payment_total: "0.00", cost_total: "0.00" })));
    vi.stubGlobal("fetch", fetch);
    await getDatevSources(config);
    const url = new URL(fetch.mock.calls[0][0], "https://test.local");
    expect(url.pathname).toBe("/api/reports/datev/sources");
    expect(Object.fromEntries(url.searchParams)).toEqual({ property_id: "p 1", period_start: "2026-01-01", period_end: "2026-12-31" });
    await getDatevSources({ ...config, accounting_scope_id: "scope/1" });
    expect(new URL(fetch.mock.calls[1][0], "https://test.local").searchParams.get("accounting_scope_id")).toBe("scope/1");
  });
  it("posts the explicit config unchanged, preserving identifiers and money strings", async () => {
    const response = { rows: [], payment_total: "12345678901234567890.91", cost_total: "-0.09", source_digest: "digest" };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(response)));
    vi.stubGlobal("fetch", fetch);
    expect(await previewDatev(config)).toEqual(response);
    expect(fetch).toHaveBeenCalledWith("/api/reports/datev/preview", expect.objectContaining({ method: "POST", credentials: "include", body: JSON.stringify(config) }));
  });
  it("downloads the server blob using POST and the reviewed digest", async () => {
    const bytes = new Uint8Array([0xef, 0xbb, 0xbf, 0x45, 0x58, 0x54, 0x46]);
    const fetch = vi.fn().mockResolvedValue(new Response(bytes, { headers: { "Content-Type": "text/csv; charset=utf-8" } }));
    vi.stubGlobal("fetch", fetch);
    const blob = await downloadDatevCsv({ ...config, expected_source_digest: "digest" });
    expect(new Uint8Array(await blob.arrayBuffer())).toEqual(bytes);
    expect(fetch).toHaveBeenCalledWith("/api/reports/datev.csv", expect.objectContaining({ method: "POST", credentials: "include", body: JSON.stringify({ ...config, expected_source_digest: "digest" }) }));
  });
  it.each([403, 409, 422])("preserves server errors (%s) instead of producing a file", async status => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Erneut prüfen" }), { status })));
    await expect(downloadDatevCsv({ ...config, expected_source_digest: "old" })).rejects.toEqual(new ApiError(status, "Erneut prüfen"));
  });
});
