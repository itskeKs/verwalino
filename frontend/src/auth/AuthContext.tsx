/**
 * Authentication context -- holds session state derived from the backend.
 *
 * On login, the backend sets an HTTP-only cookie; the frontend stores the
 * user_id, organization_id and role from the login response in React state.
 * On page reload, we probe GET /api/organizations/me to check if the session
 * cookie is still valid.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import type { LoginResponse } from "../api/types";

interface AuthState {
  /** True while we check the session on first load. */
  loading: boolean;
  /** Non-null when authenticated. */
  session: LoginResponse | null;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthCtx = createContext<AuthContextValue | null>(null);

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
        const org = await endpoints.getMyOrganization();
        // We got an org back, so the cookie is valid.
        // We don't have the full LoginResponse from the cookie alone,
        // but we can reconstruct enough from what the cookie contains.
        // The role is not exposed by /organizations/me, so we store
        // a partial session; the role will be set on explicit login.
        // For a reload scenario, we re-fetch the session info.
        if (!cancelled) {
          // Try to recover the role from a previous sessionStorage entry.
          const cached = sessionStorage.getItem("hv_session");
          if (cached) {
            try {
              const parsed: LoginResponse = JSON.parse(cached);
              setState({ loading: false, session: parsed });
              return;
            } catch {
              // fall through
            }
          }
          // Minimal fallback -- we know the org but not the role.
          // The user will need to log in again for full context, but at
          // least they stay on the page.
          setState({
            loading: false,
            session: {
              user_id: "",
              organization_id: org.id,
              role: "UNKNOWN",
            },
          });
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
    sessionStorage.setItem("hv_session", JSON.stringify(resp));
    setState({ loading: false, session: resp });
  }, []);

  const logoutFn = useCallback(async () => {
    await endpoints.logout();
    sessionStorage.removeItem("hv_session");
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

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthCtx);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}
