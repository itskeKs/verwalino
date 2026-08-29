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
});
