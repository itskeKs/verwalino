import { createContext, useContext } from "react";
import type { LoginResponse, SecondFactorLoginResponse } from "../api/types";

export interface AuthState {
  /** True while the current server-side session is checked on first load. */
  loading: boolean;
  /** Non-null when the server has an authenticated session. */
  session: LoginResponse | null;
}

export interface AuthContextValue extends AuthState {
  /**
   * Password step. Resolves to the server answer: when a second factor is
   * enrolled the answer carries `second_factor_required` and no session is
   * stored yet; a hosted account without a factor stores a restricted
   * session that only reaches the enrolment flow (issue #208).
   */
  login: (email: string, password: string) => Promise<LoginResponse>;
  /** Second login step for an enrolled factor; stores the full session. */
  completeSecondFactor: (body: { pending_token: string; code?: string; recovery_code?: string }) => Promise<SecondFactorLoginResponse>;
  /** Re-reads the server session, e.g. after enrolment lifts a restriction. */
  reloadSession: () => Promise<void>;
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
