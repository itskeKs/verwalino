import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api/client";

export interface Page<T> { items: T[]; next_cursor: string | null }

export function usePagedList<T extends { id: string }>(endpoint: string) {
  const [items, setItems] = useState<T[]>([]);
  const [query, setQuery] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const request = useRef<AbortController | null>(null);

  const fetchPage = useCallback((controller: AbortController, cursor?: string) => {
    const url = new URL(endpoint, window.location.origin);
    url.searchParams.set("q", query);
    if (cursor) url.searchParams.set("cursor", cursor);
    return api.get<Page<T>>(`${url.pathname}${url.search}`, { signal: controller.signal });
  }, [endpoint, query]);

  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    void Promise.resolve().then(async () => {
      if (controller.signal.aborted) return;
      setLoading(true);
      setError(null);
      setItems([]);
      setNextCursor(null);
      try {
        const page = await fetchPage(controller);
        if (!controller.signal.aborted) {
          setItems(page.items);
          setNextCursor(page.next_cursor);
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(String(e));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    });
    return () => { controller.abort(); request.current?.abort(); };
  }, [fetchPage, revision]);

  const loadMore = useCallback(async () => {
    if (loading || !nextCursor) return;
    // Cancel on query change or unmount, including a pending continuation.
    const controller = new AbortController();
    request.current?.abort();
    request.current = controller;
    setLoading(true);
    setError(null);
    try {
      const page = await fetchPage(controller, nextCursor);
      if (!controller.signal.aborted) {
        setItems(previous => [...new Map([...previous, ...page.items].map(item => [item.id, item])).values()]);
        setNextCursor(page.next_cursor);
      }
    } catch (e) {
      if (!controller.signal.aborted) setError(String(e));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [fetchPage, loading, nextCursor]);

  const reload = useCallback(() => setRevision(value => value + 1), []);
  return { items, query, setQuery, loading, error, nextCursor, loadMore, reload };
}
