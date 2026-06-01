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

// --- M1 Stammdaten ---

export type ManagementType = "MIET" | "WEG" | "SEV" | "MISCH";

export interface Property {
  id: string;
  name: string;
  street: string;
  zip_code: string;
  city: string;
  management_type: ManagementType;
  fiscal_year_start_month: number;
}

export interface PropertyCreate {
  name: string;
  street?: string;
  zip_code?: string;
  city?: string;
  management_type: ManagementType;
  fiscal_year_start_month?: number;
}

export interface PropertyUpdate {
  name?: string;
  street?: string;
  zip_code?: string;
  city?: string;
  management_type?: ManagementType;
  fiscal_year_start_month?: number;
}

export type UsageType = "WOHNUNG" | "GEWERBE" | "STELLPLATZ" | "SONSTIGE";

export interface Unit {
  id: string;
  property_id: string;
  number: string;
  location: string;
  area: string | null;
  heated_area: string | null;
  usage_type: UsageType;
  mea: string | null;
}

export interface UnitCreate {
  property_id: string;
  number: string;
  location?: string;
  area?: string | null;
  heated_area?: string | null;
  usage_type: UsageType;
  mea?: string | null;
}

export interface UnitUpdate {
  number?: string;
  location?: string;
  area?: string | null;
  heated_area?: string | null;
  usage_type?: UsageType;
  mea?: string | null;
}

export interface Person {
  id: string;
  first_name: string;
  last_name: string;
  company_name: string | null;
  email: string | null;
  phone: string | null;
  street: string;
  zip_code: string;
  city: string;
  iban: string | null;
  bic: string | null;
  bank_name: string | null;
}

export interface PersonCreate {
  first_name?: string;
  last_name: string;
  company_name?: string | null;
  email?: string | null;
  phone?: string | null;
  street?: string;
  zip_code?: string;
  city?: string;
  iban?: string | null;
  bic?: string | null;
  bank_name?: string | null;
}

export interface PersonUpdate {
  first_name?: string;
  last_name?: string;
  company_name?: string | null;
  email?: string | null;
  phone?: string | null;
  street?: string;
  zip_code?: string;
  city?: string;
  iban?: string | null;
  bic?: string | null;
  bank_name?: string | null;
}

export interface OwnerProfile {
  id: string;
  person_id: string;
  notes: string;
}

export interface TenantProfile {
  id: string;
  person_id: string;
  notes: string;
}

export interface Vendor {
  id: string;
  person_id: string;
  tax_id: string | null;
  vat_id: string | null;
  notes: string;
}

export interface OwnershipRecord {
  id: string;
  unit_id: string;
  owner_profile_id: string;
  valid_from: string;
  valid_to: string | null;
  mea_share: string | null;
}

export interface OwnershipCreate {
  unit_id: string;
  owner_profile_id: string;
  valid_from: string;
  valid_to?: string | null;
  mea_share?: string | null;
}

export interface LeaseRecord {
  id: string;
  unit_id: string;
  tenant_profile_id: string;
  valid_from: string;
  valid_to: string | null;
  base_rent: string;
  advance_payment: string;
  deposit: string | null;
  notes: string;
}

export interface LeaseCreate {
  unit_id: string;
  tenant_profile_id: string;
  valid_from: string;
  valid_to?: string | null;
  base_rent: string;
  advance_payment: string;
  deposit?: string | null;
  notes?: string;
}
