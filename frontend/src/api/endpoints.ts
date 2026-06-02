/**
 * Typed API calls against the backend.
 */

import { api, uploadFormData } from "./client";
import type {
  LoginRequest,
  LoginResponse,
  Organization,
  Mandate,
  Property,
  PropertyCreate,
  PropertyUpdate,
  Unit,
  UnitCreate,
  UnitUpdate,
  Person,
  PersonCreate,
  PersonUpdate,
  OwnerProfile,
  TenantProfile,
  Vendor,
  OwnershipRecord,
  OwnershipCreate,
  LeaseRecord,
  LeaseCreate,
  CostCategory,
  CostEntry,
  CostEntryCreate,
  CostEntryUpdate,
  CostEntryCorrectionCreate,
  InvoiceDocument,
  VerifyResult,
  SettlementRun,
  SettlementRunCreate,
  AdvancePaymentsUpdate,
  ComputeRequest,
  EconomicPlan,
  EconomicPlanCreate,
  PlanComputeRequest,
  WegRun,
  WegRunCreate,
  WegComputeRequest,
  AssetReportCreate,
  AssetReportComputeRequest,
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

// --- Properties ---

export function listProperties(): Promise<Property[]> {
  return api.get<Property[]>("/api/properties");
}

export function getProperty(id: string): Promise<Property> {
  return api.get<Property>(`/api/properties/${id}`);
}

export function createProperty(body: PropertyCreate): Promise<Property> {
  return api.post<Property>("/api/properties", body);
}

export function updateProperty(id: string, body: PropertyUpdate): Promise<Property> {
  return api.put<Property>(`/api/properties/${id}`, body);
}

// --- Units ---

export function listUnits(propertyId?: string): Promise<Unit[]> {
  const qs = propertyId ? `?property_id=${propertyId}` : "";
  return api.get<Unit[]>(`/api/units${qs}`);
}

export function getUnit(id: string): Promise<Unit> {
  return api.get<Unit>(`/api/units/${id}`);
}

export function createUnit(body: UnitCreate): Promise<Unit> {
  return api.post<Unit>("/api/units", body);
}

export function updateUnit(id: string, body: UnitUpdate): Promise<Unit> {
  return api.put<Unit>(`/api/units/${id}`, body);
}

// --- Persons ---

export function listPersons(): Promise<Person[]> {
  return api.get<Person[]>("/api/persons");
}

export function getPerson(id: string): Promise<Person> {
  return api.get<Person>(`/api/persons/${id}`);
}

export function createPerson(body: PersonCreate): Promise<Person> {
  return api.post<Person>("/api/persons", body);
}

export function updatePerson(id: string, body: PersonUpdate): Promise<Person> {
  return api.put<Person>(`/api/persons/${id}`, body);
}

// --- Owner Profiles ---

export function listOwnerProfiles(personId?: string): Promise<OwnerProfile[]> {
  const qs = personId ? `?person_id=${personId}` : "";
  return api.get<OwnerProfile[]>(`/api/owner-profiles${qs}`);
}

export function createOwnerProfile(personId: string, notes?: string): Promise<OwnerProfile> {
  return api.post<OwnerProfile>("/api/owner-profiles", { person_id: personId, notes: notes ?? "" });
}

// --- Tenant Profiles ---

export function listTenantProfiles(personId?: string): Promise<TenantProfile[]> {
  const qs = personId ? `?person_id=${personId}` : "";
  return api.get<TenantProfile[]>(`/api/tenant-profiles${qs}`);
}

export function createTenantProfile(personId: string, notes?: string): Promise<TenantProfile> {
  return api.post<TenantProfile>("/api/tenant-profiles", { person_id: personId, notes: notes ?? "" });
}

// --- Vendors ---

export function listVendors(personId?: string): Promise<Vendor[]> {
  const qs = personId ? `?person_id=${personId}` : "";
  return api.get<Vendor[]>(`/api/vendors${qs}`);
}

export function createVendor(body: { person_id: string; tax_id?: string; vat_id?: string; notes?: string }): Promise<Vendor> {
  return api.post<Vendor>("/api/vendors", body);
}

// --- Ownership History ---

export function listOwnership(unitId?: string): Promise<OwnershipRecord[]> {
  const qs = unitId ? `?unit_id=${unitId}` : "";
  return api.get<OwnershipRecord[]>(`/api/ownership${qs}`);
}

export function createOwnership(body: OwnershipCreate): Promise<OwnershipRecord> {
  return api.post<OwnershipRecord>("/api/ownership", body);
}

// --- Leases ---

export function listLeases(unitId?: string): Promise<LeaseRecord[]> {
  const qs = unitId ? `?unit_id=${unitId}` : "";
  return api.get<LeaseRecord[]>(`/api/leases${qs}`);
}

export function createLease(body: LeaseCreate): Promise<LeaseRecord> {
  return api.post<LeaseRecord>("/api/leases", body);
}

// --- M2: Cost Categories ---

export function listCostCategories(): Promise<CostCategory[]> {
  return api.get<CostCategory[]>("/api/cost-categories");
}

// --- M2: Cost Entries ---

export function listCostEntries(params?: {
  status?: string;
  property_id?: string;
}): Promise<CostEntry[]> {
  const parts: string[] = [];
  if (params?.status) parts.push(`status=${encodeURIComponent(params.status)}`);
  if (params?.property_id) parts.push(`property_id=${encodeURIComponent(params.property_id)}`);
  const qs = parts.length > 0 ? `?${parts.join("&")}` : "";
  return api.get<CostEntry[]>(`/api/cost-entries${qs}`);
}

export function getCostEntry(id: string): Promise<CostEntry> {
  return api.get<CostEntry>(`/api/cost-entries/${id}`);
}

export function createCostEntry(body: CostEntryCreate): Promise<CostEntry> {
  return api.post<CostEntry>("/api/cost-entries", body);
}

export function updateCostEntry(id: string, body: CostEntryUpdate): Promise<CostEntry> {
  return api.patch<CostEntry>(`/api/cost-entries/${id}`, body);
}

export function confirmCostEntry(id: string): Promise<CostEntry> {
  return api.post<CostEntry>(`/api/cost-entries/${id}/confirm`);
}

export function rejectCostEntry(id: string): Promise<CostEntry> {
  return api.post<CostEntry>(`/api/cost-entries/${id}/reject`);
}

export function createCostEntryCorrection(
  id: string,
  body: CostEntryCorrectionCreate,
): Promise<CostEntry> {
  return api.post<CostEntry>(`/api/cost-entries/${id}/correction`, body);
}

// --- M2: Invoice Documents ---

export function listInvoiceDocuments(costEntryId?: string): Promise<InvoiceDocument[]> {
  const qs = costEntryId ? `?cost_entry_id=${encodeURIComponent(costEntryId)}` : "";
  return api.get<InvoiceDocument[]>(`/api/invoice-documents${qs}`);
}

export function getInvoiceDocument(id: string): Promise<InvoiceDocument> {
  return api.get<InvoiceDocument>(`/api/invoice-documents/${id}`);
}

export function uploadInvoiceDocument(
  file: File,
  costEntryId?: string,
): Promise<InvoiceDocument> {
  const fd = new FormData();
  fd.append("file", file);
  if (costEntryId) fd.append("cost_entry_id", costEntryId);
  return uploadFormData<InvoiceDocument>("/api/invoice-documents/upload", fd);
}

export function verifyInvoiceDocument(id: string): Promise<VerifyResult> {
  return api.get<VerifyResult>(`/api/invoice-documents/${id}/verify`);
}

export function createInvoiceDocumentVersion(
  id: string,
  file: File,
  costEntryId?: string,
): Promise<InvoiceDocument> {
  const fd = new FormData();
  fd.append("file", file);
  if (costEntryId) fd.append("cost_entry_id", costEntryId);
  return uploadFormData<InvoiceDocument>(`/api/invoice-documents/${id}/version`, fd);
}

/**
 * Returns an authenticated URL for streaming document content.
 * Usage: open in new window or set as iframe src (cookie-based auth).
 */
export function invoiceDocumentContentUrl(id: string): string {
  return `/api/invoice-documents/${id}/content`;
}

// --- M3: Settlements ---

export function listSettlements(propertyId?: string): Promise<SettlementRun[]> {
  const qs = propertyId ? `?property_id=${encodeURIComponent(propertyId)}` : "";
  return api.get<SettlementRun[]>(`/api/settlements${qs}`);
}

export function getSettlement(id: string): Promise<SettlementRun> {
  return api.get<SettlementRun>(`/api/settlements/${id}`);
}

export function createSettlement(body: SettlementRunCreate): Promise<SettlementRun> {
  return api.post<SettlementRun>("/api/settlements", body);
}

export function setAdvancePayments(
  id: string,
  body: AdvancePaymentsUpdate,
): Promise<SettlementRun> {
  return api.patch<SettlementRun>(`/api/settlements/${id}/advance-payments`, body);
}

export function computeSettlement(
  id: string,
  body?: ComputeRequest,
): Promise<SettlementRun> {
  return api.post<SettlementRun>(`/api/settlements/${id}/compute`, body ?? null);
}

export function previewSettlement(
  id: string,
  body?: ComputeRequest,
): Promise<SettlementRun> {
  return api.post<SettlementRun>(`/api/settlements/${id}/preview`, body ?? null);
}

export function finalizeSettlement(
  id: string,
  body?: ComputeRequest,
): Promise<SettlementRun> {
  return api.post<SettlementRun>(`/api/settlements/${id}/finalize`, body ?? null);
}

export function createSettlementCorrection(id: string): Promise<SettlementRun> {
  return api.post<SettlementRun>(`/api/settlements/${id}/correction`);
}

/**
 * Returns an authenticated URL for the settlement PDF (finalized runs only).
 * Usage: open in new window (cookie-based auth).
 */
export function settlementPdfUrl(id: string): string {
  return `/api/settlements/${id}/pdf`;
}

// --- M4: Economic Plans (Wirtschaftsplan) ---

export function listEconomicPlans(propertyId?: string): Promise<EconomicPlan[]> {
  const qs = propertyId ? `?property_id=${encodeURIComponent(propertyId)}` : "";
  return api.get<EconomicPlan[]>(`/api/economic-plans${qs}`);
}

export function getEconomicPlan(id: string): Promise<EconomicPlan> {
  return api.get<EconomicPlan>(`/api/economic-plans/${id}`);
}

export function createEconomicPlan(body: EconomicPlanCreate): Promise<EconomicPlan> {
  return api.post<EconomicPlan>("/api/economic-plans", body);
}

export function computeEconomicPlan(id: string, body: PlanComputeRequest): Promise<EconomicPlan> {
  return api.post<EconomicPlan>(`/api/economic-plans/${id}/compute`, body);
}

export function finalizeEconomicPlan(id: string, body: PlanComputeRequest): Promise<EconomicPlan> {
  return api.post<EconomicPlan>(`/api/economic-plans/${id}/finalize`, body);
}

export function createEconomicPlanCorrection(id: string): Promise<EconomicPlan> {
  return api.post<EconomicPlan>(`/api/economic-plans/${id}/correction`);
}

export function economicPlanPdfUrl(id: string): string {
  return `/api/economic-plans/${id}/pdf`;
}

// --- M4: WEG-Jahresabrechnung ---

export function listWegRuns(runType: string, propertyId?: string): Promise<WegRun[]> {
  const parts: string[] = [`run_type=${encodeURIComponent(runType)}`];
  if (propertyId) parts.push(`property_id=${encodeURIComponent(propertyId)}`);
  return api.get<WegRun[]>(`/api/weg-settlements?${parts.join("&")}`);
}

export function getWegRun(id: string): Promise<WegRun> {
  return api.get<WegRun>(`/api/weg-settlements/${id}`);
}

export function createWegRun(body: WegRunCreate): Promise<WegRun> {
  return api.post<WegRun>("/api/weg-settlements", body);
}

export function computeWegRun(id: string, body: WegComputeRequest): Promise<WegRun> {
  return api.post<WegRun>(`/api/weg-settlements/${id}/compute`, body);
}

export function finalizeWegRun(id: string, body: WegComputeRequest): Promise<WegRun> {
  return api.post<WegRun>(`/api/weg-settlements/${id}/finalize`, body);
}

export function wegRunPdfUrl(id: string): string {
  return `/api/weg-settlements/${id}/pdf`;
}

// --- M4: Vermoegensbericht (asset report) ---

export function createAssetReport(body: AssetReportCreate): Promise<WegRun> {
  return api.post<WegRun>("/api/weg-settlements/asset-reports", body);
}

export function computeAssetReport(id: string, body: AssetReportComputeRequest): Promise<WegRun> {
  return api.post<WegRun>(`/api/weg-settlements/asset-reports/${id}/compute`, body);
}

export function finalizeAssetReport(id: string, body: AssetReportComputeRequest): Promise<WegRun> {
  return api.post<WegRun>(`/api/weg-settlements/asset-reports/${id}/finalize`, body);
}

export function assetReportPdfUrl(id: string): string {
  return `/api/weg-settlements/asset-reports/${id}/pdf`;
}
