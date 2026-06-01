/**
 * API response types -- mirrors the Pydantic models from the backend.
 */

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  user_id: string;
  organization_id: string;
  role: string;
}

export interface Organization {
  id: string;
  name: string;
}

export interface Mandate {
  id: string;
  mandate_type: "WEG" | "MIET" | "SEV";
  label: string;
  start_date: string;
  end_date: string | null;
}

export interface User {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
}
