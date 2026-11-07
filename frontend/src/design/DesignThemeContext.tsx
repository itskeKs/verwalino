import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { getPreferences, updatePreferences } from "../api/endpoints";
import type { DesignTheme } from "../api/types";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { DesignThemeCtx } from "./useDesignTheme";

interface State { owner: string; theme: DesignTheme; loading: boolean; loaded: boolean; saving: boolean; error: string | null }
interface RequestScope { identity: string; active: boolean; saving: boolean; controller: AbortController }
const empty = (owner: string): State => ({ owner, theme: "klar", loading: !!owner, loaded: false, saving: false, error: null });

export function DesignThemeProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const identity = session ? `${session.organization_id}:${session.user_id}` : "";
  const [state, setState] = useState<State>(() => empty(identity));
  const [revision, setRevision] = useState(0);
  const current = useRef<RequestScope | null>(null);
  const visible = state.owner === identity ? state : empty(identity);

  useLayoutEffect(() => {
    const scope: RequestScope = { identity, active: true, saving: false, controller: new AbortController() };
    current.current = scope;
    setState(empty(identity));
    if (identity) {
      void getPreferences(scope.controller.signal).then(preferences => {
        if (!scope.active) return;
        if (preferences.design_theme !== "klar" && preferences.design_theme !== "warm") throw new Error("Invalid design");
        setState({ owner: identity, theme: preferences.design_theme, loading: false, loaded: true, saving: false, error: null });
      }).catch(() => {
        if (scope.active) setState({ ...empty(identity), loading: false, error: "Design konnte nicht geladen werden." });
      });
    }
    return () => { scope.active = false; scope.controller.abort(); };
  }, [identity, revision]);

  useEffect(() => {
    document.documentElement.dataset.designTheme = visible.theme;
    return () => { document.documentElement.dataset.designTheme = "klar"; };
  }, [visible.theme]);

  const save = useCallback(async (theme: DesignTheme) => {
    const scope = current.current;
    if (!scope?.active || scope.identity !== identity || scope.saving || !state.loaded || state.owner !== identity) return false;
    scope.saving = true;
    setState(value => ({ ...value, saving: true, error: null }));
    try {
      const preferences = await updatePreferences(theme, scope.controller.signal);
      if (!scope.active) return false;
      setState(value => ({ ...value, theme: preferences.design_theme, saving: false, error: null }));
      return true;
    } catch (reason) {
      if (scope.active) setState(value => ({ ...value, saving: false, error: reason instanceof ApiError ? reason.message : "Design konnte nicht gespeichert werden. Bitte erneut versuchen." }));
      return false;
    } finally { scope.saving = false; }
  }, [identity, state.loaded, state.owner]);
  const reload = useCallback(() => setRevision(value => value + 1), []);

  return <DesignThemeCtx.Provider value={{ ...visible, save, reload }}>{children}</DesignThemeCtx.Provider>;
}
