import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, uploadFormData } from "./client";

function response(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  it("includes credentials for JSON requests", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ ok: true }));

    await api.get<{ ok: boolean }>("/api/example");

    expect(fetch).toHaveBeenCalledWith(
      "/api/example",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("turns JSON errors into ApiError with status and detail", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ detail: "Invalid credentials" }, 401));

    const error = await api.post("/api/auth/login", { email: "a@example.test" }).catch((reason) => reason);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      name: "ApiError",
      status: 401,
      message: "Invalid credentials",
    } satisfies Partial<ApiError>);
  });

  it("returns undefined for 204 responses", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }));

    await expect(api.delete("/api/example")).resolves.toBeUndefined();
  });

  it.each([false, true])("shows the server request ID in existing error messages (upload=%s)", async (upload) => {
    const result = response({ detail: "Internal Server Error", request_id: "failure-1234" }, 500);
    result.headers.set("X-Request-ID", "failure-1234");
    vi.mocked(fetch).mockResolvedValue(result);
    const pending = upload ? uploadFormData("/api/example", new FormData()) : api.get("/api/example");
    await expect(pending).rejects.toMatchObject({ message: "Internal Server Error (Request-ID: failure-1234)" });
  });

  it("does not force a JSON content type for multipart requests", async () => {
    vi.mocked(fetch).mockResolvedValue(response({ id: "upload-1" }));
    const body = new FormData();
    body.append("file", new File(["content"], "example.pdf", { type: "application/pdf" }));

    await uploadFormData("/api/example", body);

    expect(fetch).toHaveBeenCalledWith(
      "/api/example",
      expect.objectContaining({
        credentials: "include",
        body,
      }),
    );
    expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toBeUndefined();
  });

  it.each([false, true])("keeps errors useful when a 500 has no request ID header (upload=%s)", async (upload) => {
    vi.mocked(fetch).mockResolvedValue(response({ detail: "Internal Server Error" }, 500));
    const pending = upload ? uploadFormData("/api/example", new FormData()) : api.get("/api/example");
    await expect(pending).rejects.toMatchObject({ status: 500, message: "Internal Server Error" });
  });

  it.each([false, true])("does not show the request ID on 4xx errors (upload=%s)", async (upload) => {
    const result = response({ detail: "Conflict" }, 409);
    result.headers.set("X-Request-ID", "conflict-1234");
    vi.mocked(fetch).mockResolvedValue(result);
    const pending = upload ? uploadFormData("/api/example", new FormData()) : api.get("/api/example");
    await expect(pending).rejects.toMatchObject({ status: 409, message: "Conflict" });
  });
});

it('remembers only a valid server error request ID until login or logout', async () => {
  vi.stubGlobal('fetch', vi.fn());
  const { getLastErrorRequestId } = await import('./client');
  const error = response({ detail: 'Internal Server Error' }, 500);
  error.headers.set('X-Request-ID', 'last-failure-123456');
  vi.mocked(fetch).mockResolvedValue(error);
  await api.get('/api/example').catch(() => {});
  expect(getLastErrorRequestId()).toBe('last-failure-123456');
  vi.mocked(fetch).mockResolvedValue(response({}, 200));
  await api.post('/api/auth/logout');
  expect(getLastErrorRequestId()).toBeNull();
});
