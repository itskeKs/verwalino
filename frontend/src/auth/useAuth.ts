import { createContext, useContext } from "react";
import type { LoginResponse } from "../api/types";

export interface AuthState {
  /** True while the current server-side session is checked on first load. */
  loading: boolean;
  /** Non-null when the server has an authenticated session. */
  session: LoginResponse | null;
}

export interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const AuthCtx = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthCtx);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}
