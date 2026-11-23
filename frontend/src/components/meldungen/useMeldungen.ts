import { createContext, useContext } from "react";
import type { Meldung } from "../../api/types";

export interface MeldungenState {
  /** The Meldungen of the last answer; null until the first answer has arrived. */
  items: Meldung[] | null;
  /** The last request failed; `items` keeps what the answer before it said. */
  failed: boolean;
  /** Asks the server again, for example after the page changed the state of an import. */
  reload: () => void;
}

/** Outside the application frame (a page rendered on its own) there is nothing to ask. */
export const MeldungenCtx = createContext<MeldungenState>({ items: null, failed: false, reload: () => undefined });

export function useMeldungen(): MeldungenState {
  return useContext(MeldungenCtx);
}
