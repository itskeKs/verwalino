/**
 * Authentication context -- holds session state derived from the backend.
 *
 * On login, the backend sets an HTTP-only cookie; the frontend stores the
 * server response in React state. On page reload, it probes the dedicated
 * session endpoint, so it never recovers roles from browser storage.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import { AuthCtx, type AuthState } from "./useAuth";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    loading: true,
    session: null,
  });

  // On mount: probe the backend to see if the session cookie is still valid.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const session = await endpoints.getCurrentSession();
        if (!cancelled) {
          setState({ loading: false, session });
        }
      } catch (err) {
        // 401 means not authenticated -- that is expected.
        if (!cancelled) {
          setState({ loading: false, session: null });
        }
        // Rethrow unexpected errors so they surface.
        if (err instanceof ApiError && err.status !== 401) {
          console.error("Session probe failed:", err);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loginFn = useCallback(async (email: string, password: string) => {
    const resp = await endpoints.login({ email, password });
    setState({ loading: false, session: resp });
  }, []);

  const logoutFn = useCallback(async () => {
    await endpoints.logout();
    setState({ loading: false, session: null });
  }, []);

  return (
    <AuthCtx.Provider
      value={{ ...state, login: loginFn, logout: logoutFn }}
    >
      {children}
    </AuthCtx.Provider>
  );
}
