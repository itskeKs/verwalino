import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { listMeldungen } from "../../api/endpoints";
import type { Meldung } from "../../api/types";
import { MeldungenCtx } from "./useMeldungen";

// The frame asks on every navigation, and while a Meldung is waiting or running it asks again at this interval, so
// that a state change shows without a click. A user without such a Meldung causes no request between two navigations,
// and a hidden tab causes none at all.
export const MELDUNGEN_POLL_MS = 20_000;

/** Keeps the Meldungen of the signed-in user for the whole application frame: the entry's count and the list read the same answer. */
export function MeldungenProvider({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const [items, setItems] = useState<Meldung[] | null>(null);
  const [failed, setFailed] = useState(false);
  const latest = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const load = useCallback(async () => {
    const request = ++latest.current;
    try {
      const answer = await listMeldungen();
      if (!Array.isArray(answer?.items)) throw new Error("unexpected answer");
      if (!mounted.current || request !== latest.current) return;
      setItems(answer.items);
      setFailed(false);
    } catch {
      // The count of the answer before stays; the list page says that the last request failed.
      if (mounted.current && request === latest.current) setFailed(true);
    }
  }, []);

  useEffect(() => { void load(); }, [load, pathname]);

  const active = !!items?.some((item) => item.state === "WAITING" || item.state === "RUNNING");
  useEffect(() => {
    const refresh = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", refresh);
    const timer = active ? window.setInterval(refresh, MELDUNGEN_POLL_MS) : undefined;
    return () => {
      document.removeEventListener("visibilitychange", refresh);
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [active, load]);

  // Stable, so that a page can list it among its dependencies.
  const reload = useCallback(() => void load(), [load]);
  const value = useMemo(() => ({ items, failed, reload }), [items, failed, reload]);
  return <MeldungenCtx.Provider value={value}>{children}</MeldungenCtx.Provider>;
}
