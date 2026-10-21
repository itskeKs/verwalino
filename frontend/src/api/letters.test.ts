import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createLetterTemplate, issueLetters, listLetterDocuments, listLetterTemplates, updateLetterTemplate } from "./letters";
import { ApiError } from "./client";
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => { fetchMock = vi.fn(async () => new Response(JSON.stringify([]))); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => vi.unstubAllGlobals());
it("uses credentialed template and encoded property history endpoints", async () => {
  await listLetterTemplates(); expect(fetchMock.mock.calls[0][0]).toBe("/api/letters/templates");
  await listLetterDocuments("p&other=1"); expect(fetchMock.mock.calls[1][0]).toBe("/api/letters/documents?property_id=p%26other%3D1");
  expect(fetchMock.mock.calls[1][1].credentials).toBe("include");
});
it("sends only contract fields and preserves literal plaintext", async () => {
  const input = { name: "Vorlage", subject: "{{datum}}", body: "<script>plain</script>", organization_id: "spoof" };
  await createLetterTemplate(input); expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: input.name, subject: input.subject, body: input.body });
  await updateLetterTemplate("t/id", input); expect(fetchMock.mock.calls[1][0]).toBe("/api/letters/templates/t%2Fid"); expect(fetchMock.mock.calls[1][1].method).toBe("PUT");
  await issueLetters({ request_id: "r", template_id: "t", property_id: "p", as_of: "2026-10-21", organization_id: "spoof" } as Parameters<typeof issueLetters>[0]);
  expect(fetchMock.mock.calls[2][0]).toBe("/api/letters/issue");
  expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({ request_id: "r", template_id: "t", property_id: "p", as_of: "2026-10-21" });
});
it.each([403, 404, 409, 422, 500])("preserves API errors %s", async status => {
  fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "Nicht erstellt" }), { status }));
  await expect(issueLetters({ request_id: "r", template_id: "t", property_id: "p", as_of: "2026-10-21" })).rejects.toBeInstanceOf(ApiError);
});
