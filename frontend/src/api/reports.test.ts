import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { getPropertyYearReport, getPropertyYearReportCsv, getAnlageVSummary, getAnlageVSummaryCsv } from "./endpoints";

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => vi.unstubAllGlobals());
const query = { property_id: "property&one/two", year: 2026, accounting_scope_id: "scope&one/two", organization_id: "not-allowed" };
it("uses the same whitelisted scope for Anlage V JSON and CSV", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response("{}")).mockResolvedValueOnce(new Response("csv"));
  await getAnlageVSummary(query); await getAnlageVSummaryCsv(query);
  const calls = vi.mocked(fetch).mock.calls;
  const urls = calls.map(([url]) => new URL(String(url), "http://test"));
  expect(urls.map(url => url.pathname)).toEqual(["/api/reports/property-year/anlage-v", "/api/reports/property-year/anlage-v.csv"]);
  for (const url of urls) expect([...url.searchParams]).toEqual([["property_id", query.property_id], ["year", "2026"], ["accounting_scope_id", query.accounting_scope_id]]);
  for (const [, options] of calls) expect(options?.credentials).toBe("include");
});
it("uses identical whitelisted encoded report and credentialed CSV queries", async () => {
  const csv = "\uFEFFObjekt;Jahr;Bereich;Zahlungstyp;Kategorie;Umlagefähigkeit;Betrag(EUR)\r\nSonnenhof;2026;Einnahmen;Kaltmiete;;;700,00\r\n";
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ income_total: "800.00" }))).mockResolvedValueOnce(new Response(csv, { headers: { "Content-Type": "text/csv;charset=utf-8" } }));
  await expect(getPropertyYearReport(query)).resolves.toEqual({ income_total: "800.00" });
  const blob = await getPropertyYearReportCsv(query);
  expect(Array.from(new Uint8Array(await blob.arrayBuffer()))).toEqual(Array.from(new TextEncoder().encode(csv)));
  const calls = vi.mocked(fetch).mock.calls;
  const jsonUrl = new URL(String(calls[0][0]), "http://test");
  const csvUrl = new URL(String(calls[1][0]), "http://test");
  expect(jsonUrl.pathname).toBe("/api/reports/property-year"); expect(csvUrl.pathname).toBe("/api/reports/property-year.csv");
  expect([...jsonUrl.searchParams]).toEqual([["property_id", query.property_id], ["year", "2026"], ["accounting_scope_id", query.accounting_scope_id]]);
  expect(csvUrl.search).toBe(jsonUrl.search);
  for (const [, options] of calls) { expect(options?.credentials).toBe("include"); expect(options?.method ?? "GET").toBe("GET"); expect(options?.body).toBeUndefined(); }
});
it("omits absent scope rather than sending an invented identity", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response("{}")).mockResolvedValueOnce(new Response("csv"));
  await getPropertyYearReport({ property_id: "p", year: 1 }); await getPropertyYearReportCsv({ property_id: "p", year: 1 });
  expect(fetch).toHaveBeenNthCalledWith(1, "/api/reports/property-year?property_id=p&year=1", expect.anything());
  expect(fetch).toHaveBeenNthCalledWith(2, "/api/reports/property-year.csv?property_id=p&year=1", expect.anything());
});
it.each([403, 404, 409, 422])("preserves CSV HTTP status %s and backend detail", async status => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: "Export nicht verfügbar" }), { status }));
  const error = await getPropertyYearReportCsv({ property_id: "p", year: 9999 }).catch(error => error);
  expect(error).toBeInstanceOf(ApiError); expect(error).toMatchObject({ status, message: "Export nicht verfügbar" });
});
it("does not turn a network error into a successful blob", async () => {
  vi.mocked(fetch).mockRejectedValue(new TypeError("Failed to fetch"));
  await expect(getPropertyYearReportCsv({ property_id: "p", year: 2026 })).rejects.toThrow("Failed to fetch");
});
it("preserves safe structured-error handling and non-JSON status text", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ detail: [{ msg: "invalid" }] }), { status: 422 })).mockResolvedValueOnce(new Response("no access", { status: 403, statusText: "Forbidden" }));
  await expect(getPropertyYearReportCsv({ property_id: "p", year: 2026 })).rejects.toMatchObject({ status: 422, message: "Eingaben unvollständig oder ungültig. Bitte die angegebenen Felder prüfen." });
  await expect(getPropertyYearReportCsv({ property_id: "p", year: 2026 })).rejects.toMatchObject({ status: 403, message: "Forbidden" });
});
