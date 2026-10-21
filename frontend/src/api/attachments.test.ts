import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { archiveAttachment, attachmentDownloadUrl, getAttachment, listAttachments, uploadAttachment } from "./endpoints";
import { ApiError } from "./client";
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [], next_offset: null }), { status: 200 })); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());
it("uses only known list filters, encodes values and keeps session credentials", async () => {
  await listAttachments({ property_id: "p&x", unit_id: "u/1", person_id: "person", lease_agreement_id: "lease", include_archived: true, offset: 50, limit: 100, storage_uri: "secret" } as Parameters<typeof listAttachments>[0]);
  const url = new URL(fetchMock.mock.calls[0][0], "http://test");
  expect(url.pathname).toBe("/api/attachments");
  expect(Object.fromEntries(url.searchParams)).toEqual({ property_id: "p&x", unit_id: "u/1", person_id: "person", lease_agreement_id: "lease", include_archived: "true", offset: "50", limit: "100" });
  expect(fetchMock.mock.calls[0][1].credentials).toBe("include");
});
it("supports person-only lists without an invented property", async () => {
  await listAttachments({ person_id: "person" }); expect(fetchMock.mock.calls[0][0]).toBe("/api/attachments?person_id=person");
});
it("uploads original bytes using credentialed multipart and only known metadata", async () => {
  const file = new File(["%PDF-1"], "vertrag.pdf", { type: "application/pdf" });
  await uploadAttachment(file, { property_id: "p", unit_id: "u", person_id: "person", lease_agreement_id: "lease", title: "Vertrag", storage_uri: "spoof", organization_id: "spoof" } as Parameters<typeof uploadAttachment>[1]);
  const [url, options] = fetchMock.mock.calls[0]; const data = options.body as FormData;
  expect(url).toBe("/api/attachments"); expect(options.credentials).toBe("include"); expect(options.method).toBe("POST"); expect(options.headers).toBeUndefined();
  expect([...data.keys()].sort()).toEqual(["file", "lease_agreement_id", "person_id", "property_id", "title", "unit_id"]);
  expect(data.get("file")).toBe(file); expect(data.get("title")).toBe("Vertrag");
});
it("omits unassigned parents and archives via POST with the reason alone", async () => {
  await uploadAttachment(new File(["x"], "x"), { property_id: "p", title: "" });
  expect([...(fetchMock.mock.calls[0][1].body as FormData).keys()].sort()).toEqual(["file", "property_id", "title"]);
  await archiveAttachment("doc/id", "Erledigt"); const [url, options] = fetchMock.mock.calls[1];
  expect(url).toBe("/api/attachments/doc%2Fid/archive"); expect(options.method).toBe("POST"); expect(JSON.parse(options.body)).toEqual({ reason: "Erledigt" }); expect(options.credentials).toBe("include");
});
it("encodes metadata and original URLs without client-side HTML rendering", async () => {
  await getAttachment("doc/id"); expect(fetchMock.mock.calls[0][0]).toBe("/api/attachments/doc%2Fid");
  expect(attachmentDownloadUrl("doc/id", true)).toBe("/api/attachments/doc%2Fid/download?inline=true");
  expect(attachmentDownloadUrl("doc/id", false)).toBe("/api/attachments/doc%2Fid/download");
});
it.each([403, 404, 409, 413, 422])("propagates upload/archive rejection %s", async (status) => {
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ detail: "denied" }), { status }));
  await expect(uploadAttachment(new File(["x"], "x"), { property_id: "p", title: "" })).rejects.toBeInstanceOf(ApiError);
  await expect(archiveAttachment("doc", "reason")).rejects.toMatchObject({ status });
});
