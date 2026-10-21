import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { applyMasterImport, previewMasterImport, readMasterImportFile } from "./masterImport";

afterEach(() => vi.unstubAllGlobals());
const body = { csv_text: "kind;key\nproperty;house", data_audience: null };
it("posts exact CSV text and nullable audience with credentials and abort signal", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ rows: [], errors: [], counts: { properties: 0, units: 0, persons: 0 }, source_digest: "digest" })));
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  await previewMasterImport(body, controller.signal);
  expect(fetchMock).toHaveBeenCalledWith("/api/properties/import/preview", expect.objectContaining({ method: "POST", credentials: "include", body: JSON.stringify(body), signal: controller.signal }));
});
it("sends the digest and UUID in the apply JSON without coercing decimal text", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ rows: [], counts: { properties: 0, units: 0, persons: 0 } }), { status: 201 }));
  vi.stubGlobal("fetch", fetchMock);
  const input = { ...body, csv_text: "area\n12,345", expected_digest: "digest", request_id: "c48dc048-245a-4e47-9088-23b718a43d43" };
  await applyMasterImport(input);
  expect(fetchMock).toHaveBeenCalledWith("/api/properties/import/apply", expect.objectContaining({ body: JSON.stringify(input) }));
});
it.each([401, 403, 422, 503])("preserves normal API errors (%s)", async status => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Import denied" }), { status })));
  await expect(previewMasterImport(body)).rejects.toEqual(new ApiError(status, "Import denied"));
});
it("decodes UTF-8 BOM without modifying exact decimal strings", async () => {
  await expect(readMasterImportFile(new File([new Uint8Array([239, 187, 191]), "area\n12,345"], "data.csv"))).resolves.toBe("area\n12,345");
});
it("rejects invalid UTF-8 rather than replacing bytes", async () => {
  await expect(readMasterImportFile(new File([new Uint8Array([255])], "bad.csv"))).rejects.toThrow(/UTF-8/);
});
it("rejects files larger than 256 KiB before reading", async () => {
  await expect(readMasterImportFile(new File([new Uint8Array(256 * 1024 + 1)], "big.csv"))).rejects.toThrow(/256/);
});
