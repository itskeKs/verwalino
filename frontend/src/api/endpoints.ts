/**
 * Typed API calls against the backend.
 */

import { api } from "./client";
import type {
  LoginRequest,
  LoginResponse,
  Organization,
  Mandate,
} from "./types";

export function login(body: LoginRequest): Promise<LoginResponse> {
  return api.post<LoginResponse>("/api/auth/login", body);
}

export function logout(): Promise<void> {
  return api.post<void>("/api/auth/logout");
}

export function getMyOrganization(): Promise<Organization> {
  return api.get<Organization>("/api/organizations/me");
}

export function listMandates(): Promise<Mandate[]> {
  return api.get<Mandate[]>("/api/mandates");
}
