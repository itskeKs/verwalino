import { afterEach, expect, it, vi } from "vitest";
import { resetUserPassword, updateUser } from "./endpoints";
import { ApiError } from "./client";

afterEach(() => vi.unstubAllGlobals());
it("PATCHes only supplied changed fields to the encoded user identity", async () => {
  const result = { id: "id/one", email: "synthetic@example.test", display_name: "Synthetic", role: "READ_ONLY", is_active: false };
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(result)));
  vi.stubGlobal("fetch", fetchMock);
  expect(await updateUser("id/one", { is_active: false })).toEqual(result);
  expect(fetchMock).toHaveBeenCalledExactlyOnceWith("/api/users/id%2Fone", expect.objectContaining({
    method: "PATCH", credentials: "include", body: JSON.stringify({ is_active: false }),
  }));
});
it("POSTs a reset password only in the body and accepts an empty 204", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  expect(await resetUserPassword("id/one", "Synthetic123!")).toBeUndefined();
  expect(fetchMock).toHaveBeenCalledExactlyOnceWith("/api/users/id%2Fone/reset-password", expect.objectContaining({
    method: "POST", credentials: "include", body: JSON.stringify({ password: "Synthetic123!" }),
  }));
});
it.each([403, 404, 409, 422])("preserves reset %s status and server message with no retry", async status => {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ detail: "Unveränderter Servertext" }), { status })));
  vi.stubGlobal("fetch", fetchMock);
  await expect(resetUserPassword("target", "Synthetic123!")).rejects.toEqual(new ApiError(status, "Unveränderter Servertext"));
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
