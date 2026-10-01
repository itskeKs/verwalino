import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { usePagedList } from "./usePagedList";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("loads bounded pages and appends without duplicate rows", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValueOnce({ items: [{ id: "a" }], next_cursor: "next" })
    .mockResolvedValueOnce({ items: [{ id: "a" }, { id: "b" }], next_cursor: null });
  const { result } = renderHook(() => usePagedList<{ id: string }>("/api/properties/page"));
  await waitFor(() => expect(result.current.items).toEqual([{ id: "a" }]));
  await act(() => result.current.loadMore());
  expect(result.current.items).toEqual([{ id: "a" }, { id: "b" }]);
  expect(get.mock.calls[1][0]).toContain("cursor=next");
  expect(result.current.nextCursor).toBeNull();
});

it("discards a late response after the search changes", async () => {
  let resolveOld!: (value: unknown) => void;
  const get = vi.spyOn(api, "get").mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }))
    .mockResolvedValueOnce({ items: [{ id: "new" }], next_cursor: null });
  const { result } = renderHook(() => usePagedList<{ id: string }>("/api/persons/page"));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  act(() => result.current.setQuery("new"));
  await waitFor(() => expect(result.current.items).toEqual([{ id: "new" }]));
  await act(async () => resolveOld({ items: [{ id: "stale" }], next_cursor: "old" }));
  expect(result.current.items).toEqual([{ id: "new" }]);
  expect(result.current.nextCursor).toBeNull();
});
