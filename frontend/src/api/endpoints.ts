/**
 * Typed API calls against the backend.
 */

import { api, uploadFormData } from "./client";
import type {
  DeadlineItem,
  DsgvoStatus,
  DsgvoExport,
  MandateDocument,
} from "./types";
import type {
  LoginRequest,
  LoginResponse,
  Organization,
  Mandate,
  MandateCreate,
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
  BankAccount,
  BankAccountCreate,
  Payment,
  PaymentCreate,
  OpenItem,
  MietkontoResult,
  DunningNotice,
  DunningCreateTenant,
  DunningCreateOwner,
  Meeting,
  MeetingCreate,
  MeetingUpdate,
  AgendaItem,
  AgendaItemCreate,
  AgendaItemUpdate,
  Resolution,
  ResolutionCreate,
  RegisterEntry,
  ChallengeAnnotation,
} from "./types";

export function login(body: LoginRequest): Promise<LoginResponse> {
  return api.post<LoginResponse>("/api/auth/login", body);
}

export function getCurrentSession(): Promise<LoginResponse> {
  return api.get<LoginResponse>("/api/auth/session");
}

export function logout(): Promise<void> {
  return api.post<void>("/api/auth/logout");
}

export function getMyOrganization(): Promise<Organization> {
  return api.get<Organization>("/api/organizations/me");
}

export function createMandate(body: MandateCreate): Promise<Mandate> {
  return api.post<Mandate>("/api/mandates", body);
}

export function getMandate(id: string): Promise<Mandate> {
  return api.get<Mandate>(`/api/mandates/${id}`);
}

export function listMandateDocuments(mandateId: string): Promise<MandateDocument[]> {
  return api.get<MandateDocument[]>(`/api/mandates/${mandateId}/documents`);
}

export function uploadMandateDocument(
  mandateId: string,
  formData: FormData,
): Promise<MandateDocument> {
  return uploadFormData<MandateDocument>(
    `/api/mandates/${mandateId}/documents`,
    formData,
  );
}

export function mandateDocumentDownloadUrl(mandateId: string, docId: string): string {
  return `/api/mandates/${mandateId}/documents/${docId}/download`;
}

export function deleteMandateDocument(mandateId: string, docId: string): Promise<void> {
  return api.delete<void>(`/api/mandates/${mandateId}/documents/${docId}`);
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

// --- M6: Bank Accounts ---

export function listBankAccounts(propertyId?: string): Promise<BankAccount[]> {
  const qs = propertyId ? `?property_id=${encodeURIComponent(propertyId)}` : "";
  return api.get<BankAccount[]>(`/api/bank-accounts${qs}`);
}

export function getBankAccount(id: string): Promise<BankAccount> {
  return api.get<BankAccount>(`/api/bank-accounts/${id}`);
}

export function createBankAccount(body: BankAccountCreate): Promise<BankAccount> {
  return api.post<BankAccount>("/api/bank-accounts", body);
}

// --- M6: Payments ---

export function listPayments(params?: {
  bank_account_id?: string;
  lease_agreement_id?: string;
  tenant_profile_id?: string;
  owner_profile_id?: string;
  payment_type?: string;
  period_year?: number;
  status?: string;
}): Promise<Payment[]> {
  const parts: string[] = [];
  if (params?.bank_account_id) parts.push(`bank_account_id=${encodeURIComponent(params.bank_account_id)}`);
  if (params?.lease_agreement_id) parts.push(`lease_agreement_id=${encodeURIComponent(params.lease_agreement_id)}`);
  if (params?.tenant_profile_id) parts.push(`tenant_profile_id=${encodeURIComponent(params.tenant_profile_id)}`);
  if (params?.owner_profile_id) parts.push(`owner_profile_id=${encodeURIComponent(params.owner_profile_id)}`);
  if (params?.payment_type) parts.push(`payment_type=${encodeURIComponent(params.payment_type)}`);
  if (params?.period_year) parts.push(`period_year=${params.period_year}`);
  if (params?.status) parts.push(`status=${encodeURIComponent(params.status)}`);
  const qs = parts.length > 0 ? `?${parts.join("&")}` : "";
  return api.get<Payment[]>(`/api/payments${qs}`);
}

export function getPayment(id: string): Promise<Payment> {
  return api.get<Payment>(`/api/payments/${id}`);
}

export function createPayment(body: PaymentCreate): Promise<Payment> {
  return api.post<Payment>("/api/payments", body);
}

export function updatePayment(id: string, body: Partial<PaymentCreate>): Promise<Payment> {
  return api.patch<Payment>(`/api/payments/${id}`, body);
}

export function bookPayment(id: string): Promise<Payment> {
  return api.post<Payment>(`/api/payments/${id}/book`);
}

export function reversePayment(id: string, note?: string): Promise<Payment> {
  return api.post<Payment>(`/api/payments/${id}/reverse`, note ? { note } : {});
}

export function deletePayment(id: string): Promise<void> {
  return api.delete<void>(`/api/payments/${id}`);
}

// --- M6: Mietkonto ---

export function leaseStatement(
  leaseId: string,
  periodStart: string,
  periodEnd: string,
): Promise<MietkontoResult> {
  return api.get<MietkontoResult>(
    `/api/payments/mietkonto/lease/${leaseId}?period_start=${periodStart}&period_end=${periodEnd}`,
  );
}

export function ownerStatement(
  ownerProfileId: string,
  periodStart: string,
  periodEnd: string,
  economicPlanId?: string,
): Promise<MietkontoResult> {
  let qs = `?period_start=${periodStart}&period_end=${periodEnd}`;
  if (economicPlanId) qs += `&economic_plan_id=${encodeURIComponent(economicPlanId)}`;
  return api.get<MietkontoResult>(`/api/payments/mietkonto/owner/${ownerProfileId}${qs}`);
}

export function leaseOpenItems(
  leaseId: string,
  periodStart: string,
  periodEnd: string,
): Promise<OpenItem[]> {
  return api.get<OpenItem[]>(
    `/api/payments/open-items/lease/${leaseId}?period_start=${periodStart}&period_end=${periodEnd}`,
  );
}

// --- M6/M3 bridge: DERIVED advance payments ---

export function setAdvancePaymentsDerived(
  settlementRunId: string,
  periodYear: number,
): Promise<SettlementRun> {
  return api.patch<SettlementRun>(
    `/api/settlements/${settlementRunId}/advance-payments-derived?period_year=${periodYear}`,
  );
}

// --- M7: Dunning (Mahnwesen) ---

export function listDunningNotices(params?: {
  tenant_profile_id?: string;
  owner_profile_id?: string;
  level?: number;
  status?: string;
}): Promise<DunningNotice[]> {
  const parts: string[] = [];
  if (params?.tenant_profile_id) parts.push(`tenant_profile_id=${encodeURIComponent(params.tenant_profile_id)}`);
  if (params?.owner_profile_id) parts.push(`owner_profile_id=${encodeURIComponent(params.owner_profile_id)}`);
  if (params?.level) parts.push(`level=${params.level}`);
  if (params?.status) parts.push(`status=${encodeURIComponent(params.status)}`);
  const qs = parts.length > 0 ? `?${parts.join("&")}` : "";
  return api.get<DunningNotice[]>(`/api/dunning${qs}`);
}

export function getDunningNotice(id: string): Promise<DunningNotice> {
  return api.get<DunningNotice>(`/api/dunning/${id}`);
}

export function createDunningForTenant(body: DunningCreateTenant): Promise<DunningNotice> {
  return api.post<DunningNotice>("/api/dunning/tenant", body);
}

export function createDunningForOwner(body: DunningCreateOwner): Promise<DunningNotice> {
  return api.post<DunningNotice>("/api/dunning/owner", body);
}

export function issueDunningNotice(id: string): Promise<DunningNotice> {
  return api.post<DunningNotice>(`/api/dunning/${id}/issue`);
}

export function deleteDunningNotice(id: string): Promise<void> {
  return api.delete<void>(`/api/dunning/${id}`);
}

export function dunningPdfUrl(id: string): string {
  return `/api/dunning/${id}/pdf`;
}

// --- M8: Meetings (Eigentuemerversammlungen) ---

export function listMeetings(propertyId?: string): Promise<Meeting[]> {
  const qs = propertyId ? `?property_id=${encodeURIComponent(propertyId)}` : "";
  return api.get<Meeting[]>(`/api/meetings${qs}`);
}

export function getMeeting(id: string): Promise<Meeting> {
  return api.get<Meeting>(`/api/meetings/${id}`);
}

export function createMeeting(body: MeetingCreate): Promise<Meeting> {
  return api.post<Meeting>("/api/meetings", body);
}

export function updateMeeting(id: string, body: MeetingUpdate): Promise<Meeting> {
  return api.put<Meeting>(`/api/meetings/${id}`, body);
}

export function markMeetingHeld(id: string): Promise<Meeting> {
  return api.post<Meeting>(`/api/meetings/${id}/hold`);
}

export function deleteMeeting(id: string): Promise<void> {
  return api.delete<void>(`/api/meetings/${id}`);
}

// --- M8: Agenda Items (TOPs) ---

export function listAgendaItems(meetingId: string): Promise<AgendaItem[]> {
  return api.get<AgendaItem[]>(`/api/meetings/${meetingId}/agenda`);
}

export function createAgendaItem(
  meetingId: string,
  body: AgendaItemCreate,
): Promise<AgendaItem> {
  return api.post<AgendaItem>(`/api/meetings/${meetingId}/agenda`, body);
}

export function updateAgendaItem(
  itemId: string,
  body: AgendaItemUpdate,
): Promise<AgendaItem> {
  return api.put<AgendaItem>(`/api/meetings/agenda/${itemId}`, body);
}

export function deleteAgendaItem(itemId: string): Promise<void> {
  return api.delete<void>(`/api/meetings/agenda/${itemId}`);
}

// --- M8: Resolutions (Beschluesse) ---

export function listResolutions(meetingId: string): Promise<Resolution[]> {
  return api.get<Resolution[]>(`/api/meetings/${meetingId}/resolutions`);
}

export function createResolution(body: ResolutionCreate): Promise<Resolution> {
  return api.post<Resolution>("/api/meetings/resolutions", body);
}

export function deleteResolution(resolutionId: string): Promise<void> {
  return api.delete<void>(`/api/meetings/resolutions/${resolutionId}`);
}

// --- M8: Register (Beschluss-Sammlung) ---

export function enterIntoRegister(resolutionId: string): Promise<RegisterEntry> {
  return api.post<RegisterEntry>(
    `/api/meetings/resolutions/${resolutionId}/register`,
  );
}

export function listRegisterEntries(propertyId: string): Promise<RegisterEntry[]> {
  return api.get<RegisterEntry[]>(`/api/meetings/register/${propertyId}`);
}

export function annotateChallenge(
  entryId: string,
  body: ChallengeAnnotation,
): Promise<RegisterEntry> {
  return api.put<RegisterEntry>(
    `/api/meetings/register/entry/${entryId}/challenge`,
    body,
  );
}

// --- M8: PDFs ---

export function meetingProtocolPdfUrl(meetingId: string): string {
  return `/api/meetings/${meetingId}/pdf`;
}

export function registerPdfUrl(propertyId: string): string {
  return `/api/meetings/register/${propertyId}/pdf`;
}

// --- M9 Fristen ---
export function listFristen(referenceDate?: string): Promise<DeadlineItem[]> {
  const q = referenceDate ? `?reference_date=${encodeURIComponent(referenceDate)}` : "";
  return api.get<DeadlineItem[]>(`/api/fristen${q}`);
}

// --- M9 DSGVO (OWNER_ADMIN only) ---
export function getDsgvoStatus(personId: string): Promise<DsgvoStatus> {
  return api.get<DsgvoStatus>(`/api/dsgvo/persons/${personId}/status`);
}

export function getDsgvoExport(personId: string): Promise<DsgvoExport> {
  return api.get<DsgvoExport>(`/api/dsgvo/persons/${personId}/export`);
}

export function requestPersonDeletion(personId: string): Promise<DsgvoStatus> {
  return api.post<DsgvoStatus>(`/api/dsgvo/persons/${personId}/request-deletion`);
}

export function anonymizePerson(personId: string): Promise<DsgvoStatus> {
  return api.post<DsgvoStatus>(`/api/dsgvo/persons/${personId}/anonymize`);
}
