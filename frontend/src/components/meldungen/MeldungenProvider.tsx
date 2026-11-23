import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { listMeldungen } from "../../api/endpoints";
import type { Meldung } from "../../api/types";
import { MeldungenCtx } from "./useMeldungen";

// The frame asks on every navigation, and while a Meldung is waiting or running it asks again at this interval, so
// that a state change shows without a click. A user without such a Meldung causes no request between two navigations,
// and a hidden tab causes none at all. The poll also stops after a request that failed (the session ended, the server
// failed): the next navigation, a status change an import page reports or showing the tab again asks once more.
export const MELDUNGEN_POLL_MS = 20_000;

// The poll ends when the answer has not changed for this long (an import that stays queued because no worker runs would
// otherwise keep every visible tab asking); the next navigation starts it again.
export const MELDUNGEN_POLL_END_MS = 15 * 60_000;

/** Keeps the Meldungen of the signed-in user for the whole application frame: the entry's count and the list read the same answer. */
export function MeldungenProvider({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const [items, setItems] = useState<Meldung[] | null>(null);
  const [failed, setFailed] = useState(false);
  // Counts the answers, so that the poll starts over with every one, whether or not it differs from the answer before.
  const [answers, setAnswers] = useState(0);
  const latest = useRef(0);
  const mounted = useRef(true);
  // The last answer as text and when it last differed from the answer before it (or the page was last navigated to).
  const seen = useRef<string | null>(null);
  const changedAt = useRef(0);

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
      const text = JSON.stringify(answer.items);
      if (text !== seen.current) {
        seen.current = text;
        changedAt.current = Date.now();
      }
      setItems(answer.items);
      setFailed(false);
      setAnswers((count) => count + 1);
    } catch {
      // The count of the answer before stays; the list page says that the last request failed.
      if (mounted.current && request === latest.current) setFailed(true);
    }
  }, []);

  useEffect(() => {
    changedAt.current = Date.now();
    void load();
  }, [load, pathname]);

  useEffect(() => {
    const refresh = () => { if (document.visibilityState === "visible") void load(); };
    document.addEventListener("visibilitychange", refresh);
    return () => document.removeEventListener("visibilitychange", refresh);
  }, [load]);

  // The poll runs while a Meldung waits or runs and the last request did not fail. Every answer starts it over, so a
  // request never follows its predecessor closely; it ends once the answers have not changed for MELDUNGEN_POLL_END_MS.
  const active = !!items?.some((item) => item.state === "WAITING" || item.state === "RUNNING");
  useEffect(() => {
    if (!active || failed) return undefined;
    const timer = window.setInterval(() => {
      if (Date.now() - changedAt.current >= MELDUNGEN_POLL_END_MS) window.clearInterval(timer);
      else if (document.visibilityState === "visible") void load();
    }, MELDUNGEN_POLL_MS);
    return () => window.clearInterval(timer);
  }, [active, failed, answers, load]);

  // Stable, so that a page can list it among its dependencies.
  const reload = useCallback(() => void load(), [load]);
  const value = useMemo(() => ({ items, failed, reload }), [items, failed, reload]);
  return <MeldungenCtx.Provider value={value}>{children}</MeldungenCtx.Provider>;
}
