import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";

export interface Page<T> { items: T[]; next_cursor: string | null }

export function usePagedList<T extends { id: string }>(endpoint: string, { keepOnReload = false }: { keepOnReload?: boolean } = {}) {
  const [items, setItems] = useState<T[]>([]);
  const [query, setQuery] = useState("");
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const request = useRef<AbortController | null>(null);
  const previousFetch = useRef<typeof fetchPage | null>(null);
  const loadedPages = useRef(1);

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
      if (!keepOnReload || previousFetch.current !== fetchPage) {
        setItems([]);
        setNextCursor(null);
        loadedPages.current = 1;
      }
      previousFetch.current = fetchPage;
      try {
        let page = await fetchPage(controller);
        const refreshed = [...page.items];
        let count = 1;
        // Refresh the pages already opened, so saving a later row cannot collapse the list.
        while (keepOnReload && count < loadedPages.current && page.next_cursor && !controller.signal.aborted) {
          page = await fetchPage(controller, page.next_cursor);
          refreshed.push(...page.items);
          count++;
        }
        if (!controller.signal.aborted) {
          setItems([...new Map(refreshed.map(item => [item.id, item])).values()]);
          setNextCursor(page.next_cursor);
          loadedPages.current = count;
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) { setItems([]); setNextCursor(null); }
          setError(String(e));
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    });
    return () => { controller.abort(); request.current?.abort(); };
  }, [fetchPage, revision, keepOnReload]);

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
        loadedPages.current++;
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
