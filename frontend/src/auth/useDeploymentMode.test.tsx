import { StrictMode, type ReactNode } from "react";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useDeploymentMode } from "./useDeploymentMode";

describe("useDeploymentMode", () => {
  it.each(["selfhost", "hosted"])("reads %s once across rerenders", async (mode) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      deployment_mode: mode, version: "0.1.0",
    }))));
    const { result, rerender } = renderHook(() => useDeploymentMode(), {
      wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
    });
    expect(result.current.mode).toBeNull();
    await waitFor(() => expect(result.current.mode).toBe(mode));
    rerender();
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledWith("/api/meta", expect.objectContaining({ cache: "no-store" }));
    expect(result.current.error).toBe(false);
  });

  it("keeps the mode unknown when metadata cannot be loaded", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    const { result } = renderHook(() => useDeploymentMode());
    await waitFor(() => expect(result.current.error).toBe(true));
    expect(result.current.mode).toBeNull();
  });

  it("does not treat invalid metadata as a deployment mode", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      deployment_mode: "production", version: "0.1.0",
    }))));
    const { result } = renderHook(() => useDeploymentMode());
    await waitFor(() => expect(result.current.error).toBe(true));
    expect(result.current.mode).toBeNull();
  });
});
