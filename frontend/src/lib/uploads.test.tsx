import { StrictMode, type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MAX_UPLOAD_BYTES, useUploadLimit } from "./uploads";

afterEach(() => vi.unstubAllGlobals());

it.each(["selfhost", "hosted"])("uses the backend file cap in %s, once across StrictMode replay", async (mode) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    deployment_mode: mode, max_upload_bytes: 4,
  }))));
  const { result, rerender } = renderHook(() => useUploadLimit(), {
    wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
  });
  expect(result.current).toBe(MAX_UPLOAD_BYTES);
  await waitFor(() => expect(result.current).toBe(4));
  rerender();
  expect(fetch).toHaveBeenCalledOnce();
  expect(fetch).toHaveBeenCalledWith("/api/meta", expect.objectContaining({ cache: "no-store" }));
});

it.each([undefined, null, 0, -1, 1.5, "4"])("keeps the safety fallback for an absent or invalid cap %s", async (cap) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ max_upload_bytes: cap }))));
  const { result } = renderHook(() => useUploadLimit());
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  expect(result.current).toBe(MAX_UPLOAD_BYTES);
});

it("keeps the safety fallback when metadata is unavailable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
  const { result } = renderHook(() => useUploadLimit());
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  expect(result.current).toBe(MAX_UPLOAD_BYTES);
});
