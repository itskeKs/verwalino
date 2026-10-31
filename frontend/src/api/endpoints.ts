/**
 * Typed API calls against the backend.
 */

import { api, uploadFormData } from "./client";
import type { Task, TaskInput, TaskFilters, TaskPage, User, UserRole, PropertyYearReport, PropertyYearReportQuery, AnlageVSummary } from "./types";

import type { Attachment, AttachmentFilters, AttachmentPage, AttachmentUpload } from "./types";
import type { SpecialLevy, SpecialLevyCreate } from "./types";
import type { ConsumptionSuggestion, Meter, MeterCreate, MeterPatch, MeterReading, MeterReadingCreate } from "./types";
import type { SourceClassificationCommand, SourceClassificationReceipt, SourceAdmissionReviewCommand, SourceAdmissionReviewReceipt } from "./types";
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
  OrganizationQuota,
  Mandate,
  MandateCreate,
  MandateUpdate,
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
  VendorUpdate,
  OwnershipRecord,
  OwnershipCreate,
  OwnershipEndRequest,
  LeaseRecord,
  LeaseCreate,
  LeaseUpdate,
  LeaseRentChange,
  LeaseRentChangeCreate,
  CostCategory,
  CostEntry,
  CostEntryCreate,
  CostEntryUpdate,
  CostEntryCorrectionCreate,
  InvoiceDocument,
  VerifyResult,
  SettlementRun,
  SettlementRunCreate,
  SettlementBatchCreate,
  SettlementBatchResult,
  AdvancePaymentsUpdate,
  AdvanceProposal,
  AdvanceProposalApply,
  ComputeRequest,
  HeatingBaseKey,
  HeatingConsumptionCandidate,
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
  BankAccountUpdate,
  BankTransactionPage,
  BankTransactionStatus,
  BankMatchingPage,
  BankAssignmentLine,
  BankAssignmentResult,
  BankImportResult,
  Payment,
  PaymentCreate,
  DepositAccount,
  OpenItem,
  MietkontoResult,
  DunningNotice,
  DunningSettings,
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
  DeclarationImport,
  DeclarationImportCreate,
  DeclarationDocument,
  DeclarationDocumentUpdate,
  DeclarationCandidate,
  CandidateReviewRequest,
  CandidateReviewResult,
  BulkCandidateReviewRequest,
  ApprovalPreview,
  ApprovalResult,
  DeclarationApprovalRequest,
  PropertyDocument,
  PropertyKnowledgeOverview,
  PropertyRule,
  KnowledgeSearchResponse,
  KnowledgeAnswerOut,
} from "./types";

function propertyYearReportQuery(query: PropertyYearReportQuery): string {
  const params = new URLSearchParams({ property_id: query.property_id, year: String(query.year) });
  if (query.accounting_scope_id) params.set("accounting_scope_id", query.accounting_scope_id);
  return params.toString();
}

export function getPropertyYearReport(query: PropertyYearReportQuery): Promise<PropertyYearReport> {
  return api.get<PropertyYearReport>(`/api/reports/property-year?${propertyYearReportQuery(query)}`);
}

export function getPropertyYearReportCsv(query: PropertyYearReportQuery): Promise<Blob> {
  return api.getBlob(`/api/reports/property-year.csv?${propertyYearReportQuery(query)}`);
}

export function getAnlageVSummary(query: PropertyYearReportQuery): Promise<AnlageVSummary> {
  return api.get<AnlageVSummary>(`/api/reports/property-year/anlage-v?${propertyYearReportQuery(query)}`);
}

export function anlageVCsvUrl(query: PropertyYearReportQuery): string {
  return `/api/reports/property-year/anlage-v.csv?${propertyYearReportQuery(query)}`;
}

export function getAnlageVSummaryCsv(query: PropertyYearReportQuery): Promise<Blob> {
  return api.getBlob(anlageVCsvUrl(query));
}

export function login(body: LoginRequest): Promise<LoginResponse> {
  return api.post<LoginResponse>("/api/auth/login", body);
}

export function getCurrentSession(): Promise<LoginResponse> {
  return api.get<LoginResponse>("/api/auth/session");
}

export function logout(): Promise<void> {
  return api.post<void>("/api/auth/logout");
}

export interface ChangePasswordRequest {
  old_password: string;
  new_password: string;
}

export function changePassword(body: ChangePasswordRequest): Promise<void> {
  return api.post<void>("/api/auth/password", body);
}

export function getMyOrganization(): Promise<Organization> {
  return api.get<Organization>("/api/organizations/me");
}

export function getOrganizationQuota(): Promise<OrganizationQuota> {
  return api.get<OrganizationQuota>("/api/organizations/quota");
}

export function createMandate(body: MandateCreate): Promise<Mandate> {
  return api.post<Mandate>("/api/mandates", body);
}

export function updateMandate(id: string, body: MandateUpdate): Promise<Mandate> {
  return api.patch<Mandate>(`/api/mandates/${encodeURIComponent(id)}`, body);
}

export function getMandate(id: string): Promise<Mandate> {
  return api.get<Mandate>(`/api/mandates/${id}`);
}

export function listMandateDocuments(mandateId: string, includeArchived = false): Promise<MandateDocument[]> {
  return api.get<MandateDocument[]>(`/api/mandates/${mandateId}/documents?include_archived=${includeArchived}`);
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

export function archiveMandateDocument(mandateId: string, docId: string, reason: string): Promise<MandateDocument> {
  return api.post<MandateDocument>(`/api/mandates/${mandateId}/documents/${docId}/archive`, { reason });
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

export function createProperty(body: PropertyCreate, key?: string): Promise<Property> {
  return api.post<Property>("/api/properties", body, key ? { headers: { "Idempotency-Key": key } } : undefined);
}

export function updateProperty(id: string, body: PropertyUpdate): Promise<Property> {
  return api.put<Property>(`/api/properties/${id}`, body);
}

export function confirmMeaScale(
  propertyId: string,
  denominator: number,
): Promise<Property> {
  return api.post<Property>(
    `/api/properties/${propertyId}/mea-scale/confirm`,
    { denominator, expected_scale_status: "LEGACY_UNCONFIRMED", confirm_existing_values_as_numerators: true },
  );
}

// --- Teilungserklärungen ---

export function createDeclarationImport(
  body: DeclarationImportCreate,
): Promise<DeclarationImport> {
  return api.post<DeclarationImport>("/api/declaration-imports", body);
}

export function uploadDeclarationDocument(
  importId: string,
  formData: FormData,
): Promise<DeclarationDocument> {
  return uploadFormData<DeclarationDocument>(
    `/api/declaration-imports/${importId}/documents`,
    formData,
  );
}

export function listDeclarationDocuments(importId: string): Promise<DeclarationDocument[]> {
  return api.get<DeclarationDocument[]>(`/api/declaration-imports/${importId}/documents`);
}

export function updateDeclarationDocument(
  importId: string,
  documentId: string,
  body: DeclarationDocumentUpdate,
): Promise<DeclarationDocument> {
  return api.patch<DeclarationDocument>(
    `/api/declaration-imports/${importId}/documents/${documentId}`,
    body,
  );
}

export function startDeclarationImport(importId: string, requestKey: string): Promise<DeclarationImport> {
  return api.post<DeclarationImport>(`/api/declaration-imports/${importId}/start`, undefined, { headers: { "Idempotency-Key": requestKey } });
}

export function getDeclarationImport(importId: string): Promise<DeclarationImport> {
  return api.get<DeclarationImport>(`/api/declaration-imports/${importId}`);
}

export function listDeclarationCandidates(importId: string): Promise<DeclarationCandidate[]> {
  return api.get<DeclarationCandidate[]>(`/api/declaration-imports/${importId}/candidates`);
}

export function reviewDeclarationCandidate(
  importId: string,
  candidateId: string,
  body: CandidateReviewRequest,
  idempotencyKey?: string,
): Promise<CandidateReviewResult> {
  return api.patch<CandidateReviewResult>(
    `/api/declaration-imports/${importId}/candidates/${candidateId}`,
    body,
    idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : undefined,
  );
}

export function bulkReviewDeclarationCandidates(
  importId: string,
  body: BulkCandidateReviewRequest,
  idempotencyKey?: string,
): Promise<CandidateReviewResult> {
  return api.post<CandidateReviewResult>(
    `/api/declaration-imports/${importId}/candidates/bulk-review`,
    body,
    idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : undefined,
  );
}

export function getDeclarationApprovalPreview(importId: string): Promise<ApprovalPreview> {
  return api.get<ApprovalPreview>(`/api/declaration-imports/${importId}/approval-preview`);
}

export function approveDeclarationImport(
  importId: string,
  body: DeclarationApprovalRequest,
): Promise<ApprovalResult> {
  return api.post<ApprovalResult>(`/api/declaration-imports/${importId}/approve`, body);
}

export function retryDeclarationImport(importId: string, requestKey: string): Promise<DeclarationImport> {
  return api.post<DeclarationImport>(`/api/declaration-imports/${importId}/retry`, undefined, { headers: { "Idempotency-Key": requestKey } });
}

export function cancelDeclarationImport(importId: string): Promise<DeclarationImport> {
  return api.post<DeclarationImport>(`/api/declaration-imports/${importId}/cancel`);
}

export function listPropertyDocuments(propertyId: string): Promise<PropertyDocument[]> {
  return api.get<PropertyDocument[]>(`/api/properties/${propertyId}/documents`);
}

export function propertyDocumentContentUrl(
  propertyId: string,
  documentId: string,
  page?: number,
): string {
  const path = "/api/properties/" + propertyId + "/documents/" + documentId + "/content";
  return page ? path + "#page=" + page : path;
}

export function getPropertyKnowledge(
  propertyId: string,
  options: { includeHistory?: boolean; signal?: AbortSignal } = {},
): Promise<PropertyKnowledgeOverview> {
  const params = new URLSearchParams();
  if (options.includeHistory) params.set("include_history", "true");
  const query = params.size ? `?${params.toString()}` : "";
  return api.get<PropertyKnowledgeOverview>(`/api/properties/${propertyId}/knowledge${query}`, { signal: options.signal });
}

export function listPropertyRules(
  propertyId: string,
  options: { includeHistory?: boolean; signal?: AbortSignal } = {},
): Promise<PropertyRule[]> {
  const params = new URLSearchParams();
  if (options.includeHistory) params.set("include_history", "true");
  const query = params.size ? `?${params.toString()}` : "";
  return api.get<PropertyRule[]>(`/api/properties/${propertyId}/rules${query}`, { signal: options.signal });
}

export function searchPropertyKnowledge(
  propertyId: string,
  query: string,
  options: { includeHistory?: boolean; limit?: number; signal?: AbortSignal } = {},
): Promise<KnowledgeSearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (options.includeHistory) params.set("include_history", "true");
  if (options.limit) params.set("limit", String(options.limit));
  return api.get<KnowledgeSearchResponse>(
    `/api/properties/${propertyId}/knowledge/search?${params.toString()}`,
    { signal: options.signal },
  );
}

export function queryPropertyKnowledge(
  propertyId: string,
  question: string,
): Promise<KnowledgeAnswerOut> {
  return api.post<KnowledgeAnswerOut>(`/api/properties/${propertyId}/knowledge/query`, { question });
}

export function declarationImportDocumentContentUrl(
  importId: string,
  documentId: string,
  page?: number,
): string {
  const path = "/api/declaration-imports/" + importId + "/documents/" + documentId + "/content";
  return page ? path + "#page=" + page : path;
}

export const terminalDeclarationImportStatuses: ReadonlySet<DeclarationImport["status"]> = new Set([
  "NEEDS_REVIEW",
  "FAILED",
  "CANCELLED",
  "APPLIED",
]);

export interface DeclarationImportPollingOptions {
  onUpdate: (item: DeclarationImport) => void;
  onError: (error: unknown) => void;
  intervalMs?: number;
}

export function pollDeclarationImport(
  importId: string,
  { onUpdate, onError, intervalMs = 1_000 }: DeclarationImportPollingOptions,
): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;

  const poll = async (): Promise<void> => {
    let item: DeclarationImport;
    try {
      item = await getDeclarationImport(importId);
    } catch (error) {
      if (!stopped) onError(error);
      return;
    }
    if (stopped) return;
    try {
      onUpdate(item);
    } catch (error) {
      if (!stopped) onError(error);
      return;
    }
    if (stopped || terminalDeclarationImportStatuses.has(item.status)) return;
    timer = setTimeout(() => void poll(), intervalMs);
  };

  void poll();
  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
  };
}

export function deleteProperty(id: string, signal?: AbortSignal): Promise<void> {
  return api.delete<void>(`/api/properties/${encodeURIComponent(id)}`, { signal });
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

export function deleteUnit(id: string, signal?: AbortSignal): Promise<void> {
  return api.delete<void>(`/api/units/${encodeURIComponent(id)}`, { signal });
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

export function deletePerson(id: string, signal?: AbortSignal): Promise<void> {
  return api.delete<void>(`/api/persons/${encodeURIComponent(id)}`, { signal });
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

export function updateVendor(id: string, body: VendorUpdate): Promise<Vendor> {
  return api.put<Vendor>(`/api/vendors/${encodeURIComponent(id)}`, body);
}

// --- Ownership History ---

export function listOwnership(unitId?: string): Promise<OwnershipRecord[]> {
  const qs = unitId ? `?unit_id=${unitId}` : "";
  return api.get<OwnershipRecord[]>(`/api/ownership${qs}`);
}

export function createOwnership(body: OwnershipCreate): Promise<OwnershipRecord> {
  return api.post<OwnershipRecord>("/api/ownership", body);
}

export function patchOwnership(id: string, body: OwnershipEndRequest): Promise<OwnershipRecord> {
  return api.patch<OwnershipRecord>(`/api/ownership/${id}`, body);
}

// --- Leases ---

export function listLeases(unitId?: string): Promise<LeaseRecord[]> {
  const qs = unitId ? `?unit_id=${unitId}` : "";
  return api.get<LeaseRecord[]>(`/api/leases${qs}`);
}

export function createLease(body: LeaseCreate): Promise<LeaseRecord> {
  return api.post<LeaseRecord>("/api/leases", body);
}

export function patchLease(
  leaseId: string,
  body: LeaseUpdate,
): Promise<LeaseRecord> {
  return api.patch<LeaseRecord>(`/api/leases/${leaseId}`, body);
}

export function deleteLease(id: string, signal?: AbortSignal): Promise<void> {
  return api.delete<void>(`/api/leases/${encodeURIComponent(id)}`, { signal });
}

export function listLeaseRentChanges(leaseId: string): Promise<LeaseRentChange[]> {
  return api.get<LeaseRentChange[]>(`/api/leases/${leaseId}/rent-changes`);
}

export function createLeaseRentChange(
  leaseId: string,
  body: LeaseRentChangeCreate,
): Promise<LeaseRentChange> {
  return api.post<LeaseRentChange>(`/api/leases/${leaseId}/rent-changes`, body);
}

export type StaffelmieteCreate = {
  staffel_start_date: string;
  staffel_interval_months: number;
  staffel_steps: number;
} & (
  | { staffel_increase_amount: string; staffel_increase_percent?: never }
  | { staffel_increase_amount?: never; staffel_increase_percent: string }
);

export function createStaffelmieteRentChanges(
  leaseId: string,
  body: StaffelmieteCreate,
): Promise<LeaseRentChange[]> {
  return api.post<LeaseRentChange[]>(`/api/leases/${leaseId}/rent-changes/staffel`, body);
}

export interface IndexmieteInput {
  old_index: string;
  new_index: string;
  declaration_received_on: string;
}

export interface IndexmietePreview {
  valid_from: string;
  previous_valid_from: string;
  previous_base_rent: string;
  base_rent: string;
  advance_payment: string;
}

export interface IndexmieteCreate extends IndexmieteInput {
  expected_base_rent: string;
  expected_advance_payment: string;
  expected_previous_valid_from: string;
}

export function previewIndexmieteRentChange(
  leaseId: string,
  body: IndexmieteInput,
): Promise<IndexmietePreview> {
  return api.post<IndexmietePreview>(`/api/leases/${leaseId}/rent-changes/index/preview`, body);
}

export function createIndexmieteRentChange(
  leaseId: string,
  body: IndexmieteCreate,
): Promise<LeaseRentChange> {
  return api.post<LeaseRentChange>(`/api/leases/${leaseId}/rent-changes/index`, body);
}

// --- M2: Cost Categories ---

export function listCostCategories(): Promise<CostCategory[]> {
  return api.get<CostCategory[]>("/api/cost-categories");
}

// --- M2: Cost Entries ---

export interface CostEntryListQuery {
  status?: string;
  property_id?: string;
}

function knownQuery<T extends object>(query: T | undefined, keys: readonly (keyof T)[]): string {
  const parts: string[] = [];
  for (const key of keys) {
    const value = query?.[key];
    if (value !== undefined && value !== null && value !== "") {
      parts.push(`${String(key)}=${encodeURIComponent(String(value))}`);
    }
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

const COST_ENTRY_FILTERS = ["status", "property_id"] as const;

export function listCostEntries(params?: CostEntryListQuery): Promise<CostEntry[]> {
  return api.get<CostEntry[]>(`/api/cost-entries${knownQuery(params, COST_ENTRY_FILTERS)}`);
}

export function exportCostEntriesCsv(params?: CostEntryListQuery): Promise<Blob> {
  return api.getBlob(`/api/cost-entries/export.csv${knownQuery(params, COST_ENTRY_FILTERS)}`);
}

export function getCostEntry(id: string): Promise<CostEntry> {
  return api.get<CostEntry>(`/api/cost-entries/${id}`);
}

export function createCostEntry(body: CostEntryCreate, idempotencyKey?: string): Promise<CostEntry> {
  return api.post<CostEntry>("/api/cost-entries", body,
    idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : undefined);
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

export function retryInvoiceExtraction(id: string): Promise<InvoiceDocument> {
  return api.post<InvoiceDocument>(`/api/invoice-documents/${id}/retry`);
}

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

export function getAdvanceProposal(id: string): Promise<AdvanceProposal> {
  return api.get<AdvanceProposal>(`/api/settlements/${id}/advance-proposal`);
}

export function applyAdvanceProposal(
  id: string,
  body: AdvanceProposalApply,
): Promise<LeaseRentChange> {
  return api.post<LeaseRentChange>(`/api/settlements/${id}/advance-proposal`, {
    valid_from: body.valid_from,
  });
}

export function createSettlement(body: SettlementRunCreate): Promise<SettlementRun> {
  return api.post<SettlementRun>("/api/settlements", body);
}

export function createSettlementBatch(body: SettlementBatchCreate): Promise<SettlementBatchResult> {
  return api.post<SettlementBatchResult>("/api/settlements/batch", body);
}

export function setAdvancePayments(
  id: string,
  body: AdvancePaymentsUpdate,
): Promise<SettlementRun> {
  return api.patch<SettlementRun>(`/api/settlements/${id}/advance-payments`, body);
}

export function getHeatingConsumption(
  runId: string,
  costEntryId: string,
  meterType: string,
  baseKey: HeatingBaseKey,
): Promise<HeatingConsumptionCandidate[]> {
  const query = new URLSearchParams({ cost_entry_id: costEntryId, meter_type: meterType, base_key: baseKey });
  return api.get<HeatingConsumptionCandidate[]>(`/api/settlements/${encodeURIComponent(runId)}/heating-consumption?${query}`);
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

export function getWegInputOwners(id: string): Promise<string[]> {
  return api.get<string[]>(`/api/weg-settlements/${id}/input-owners`);
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

export function updateBankAccount(id: string, body: BankAccountUpdate): Promise<BankAccount> {
  return api.patch<BankAccount>(`/api/bank-accounts/${encodeURIComponent(id)}`, body);
}

export function listBankTransactions(id: string, status?: BankTransactionStatus, offset = 0): Promise<BankTransactionPage> {
  const params = new URLSearchParams({ offset: String(offset), limit: "50" });
  if (status) params.set("status", status);
  return api.get<BankTransactionPage>(`/api/bank-accounts/${encodeURIComponent(id)}/transactions?${params}`);
}

export function listBankMatchingTransactions(bankAccountId: string, status: BankTransactionStatus | "ALL" = "NEU", offset = 0): Promise<BankMatchingPage> {
  const params = new URLSearchParams({ bank_account_id: bankAccountId, status, offset: String(offset), limit: "50" });
  return api.get<BankMatchingPage>(`/api/bank-transactions?${params}`);
}

export function assignBankTransaction(id: string, lines: BankAssignmentLine[]): Promise<BankAssignmentResult> {
  return api.post<BankAssignmentResult>(`/api/bank-transactions/${encodeURIComponent(id)}/assign`, { lines });
}

export function ignoreBankTransaction(id: string): Promise<{ id: string; status: "IGNORIERT" }> {
  return api.post<{ id: string; status: "IGNORIERT" }>(`/api/bank-transactions/${encodeURIComponent(id)}/ignore`);
}

export function importBankTransactions(id: string, file: File, dryRun = true): Promise<BankImportResult> {
  const data = new FormData();
  data.append("file", file);
  return uploadFormData<BankImportResult>(`/api/bank-accounts/${encodeURIComponent(id)}/imports?dry_run=${dryRun}`, data);
}

// --- M6: Payments ---

export function listDeposits(scopeId?: string): Promise<DepositAccount[]> {
  const query = scopeId ? `?accounting_scope_id=${encodeURIComponent(scopeId)}` : "";
  return api.get<DepositAccount[]>(`/api/payments/deposits${query}`);
}

export interface PaymentListQuery {
  bank_account_id?: string;
  lease_agreement_id?: string;
  tenant_profile_id?: string;
  owner_profile_id?: string;
  payment_type?: string;
  period_year?: number;
  status?: string;
}

const PAYMENT_FILTERS = ["bank_account_id", "lease_agreement_id", "tenant_profile_id", "owner_profile_id", "payment_type", "period_year", "status"] as const;

export function listPayments(params?: PaymentListQuery): Promise<Payment[]> {
  return api.get<Payment[]>(`/api/payments${knownQuery(params, PAYMENT_FILTERS)}`);
}

export function exportPaymentsCsv(params?: PaymentListQuery): Promise<Blob> {
  return api.getBlob(`/api/payments/export.csv${knownQuery(params, PAYMENT_FILTERS)}`);
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
  accountingScopeId?: string,
): Promise<MietkontoResult> {
  return api.get<MietkontoResult>(
    `/api/payments/mietkonto/lease/${leaseId}?period_start=${periodStart}&period_end=${periodEnd}${accountingScopeId ? `&accounting_scope_id=${encodeURIComponent(accountingScopeId)}` : ""}`,
  );
}

export function ownerStatement(
  ownerProfileId: string,
  periodStart: string,
  periodEnd: string,
  economicPlanId?: string,
  accountingScopeId?: string,
): Promise<MietkontoResult> {
  let qs = `?period_start=${periodStart}&period_end=${periodEnd}`;
  if (accountingScopeId) qs += `&accounting_scope_id=${encodeURIComponent(accountingScopeId)}`;
  if (economicPlanId) qs += `&economic_plan_id=${encodeURIComponent(economicPlanId)}`;
  return api.get<MietkontoResult>(`/api/payments/mietkonto/owner/${ownerProfileId}${qs}`);
}

export function leaseOpenItems(
  leaseId: string,
  periodStart: string,
  periodEnd: string,
  accountingScopeId?: string,
): Promise<OpenItem[]> {
  return api.get<OpenItem[]>(
    `/api/payments/open-items/lease/${leaseId}?period_start=${periodStart}&period_end=${periodEnd}${accountingScopeId ? `&accounting_scope_id=${encodeURIComponent(accountingScopeId)}` : ""}`,
  );
}

export function allLeaseOpenItems(periodStart: string, periodEnd: string, accountingScopeId?: string): Promise<{ lease_agreement_id: string; items: OpenItem[] }[]> {
  const params = new URLSearchParams({ period_start: periodStart, period_end: periodEnd });
  if (accountingScopeId) params.set("accounting_scope_id", accountingScopeId);
  return api.get(`/api/payments/open-items?${params}`);
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

export function getDunningSettings(): Promise<DunningSettings> {
  return api.get<DunningSettings>("/api/dunning/settings");
}

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

export function meetingInvitationPdfUrl(meetingId: string): string {
  return `/api/meetings/${meetingId}/invitation.pdf`;
}

export function meetingProtocolPdfUrl(meetingId: string): string {
  return `/api/meetings/${meetingId}/pdf`;
}

export function registerPdfUrl(propertyId: string): string {
  return `/api/meetings/register/${propertyId}/pdf`;
}

// --- M9 Fristen ---
export function listFristen(referenceDate?: string, accountingScopeId?: string): Promise<DeadlineItem[]> {
  const query = new URLSearchParams();
  if (referenceDate) query.set("reference_date", referenceDate);
  if (accountingScopeId) query.set("accounting_scope_id", accountingScopeId);
  return api.get<DeadlineItem[]>(`/api/fristen?${query}`);
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


export const sourceAdmissionReviewApi = {
  base: "/api/property-source-admission-reviews",
  get(id: string, signal?: AbortSignal) {
    return api.get<SourceAdmissionReviewReceipt>(`${this.base}/${id}`, { signal });
  },
  review(body: SourceAdmissionReviewCommand, key: string) {
    return api.post<SourceAdmissionReviewReceipt>(this.base, body, { headers: { "Idempotency-Key": key } });
  },
};

export const sourceClassificationApi = {
  base: "/api/property-source-classifications",
  get(id: string, signal?: AbortSignal) {
    return api.get<SourceClassificationReceipt>(`${this.base}/${id}`, { signal });
  },
  classify(body: SourceClassificationCommand, key: string) {
    return api.post<SourceClassificationReceipt>(this.base, body, { headers: { "Idempotency-Key": key } });
  },
};

export function createUser(
  email: string,
  password: string,
  displayName: string,
  role: string,
): Promise<unknown> {
  return api.post<unknown>("/api/users", { email, password, display_name: displayName, role });
}

export function updateUser(id: string, body: { role?: UserRole; is_active?: boolean }): Promise<User> {
  return api.patch<User>(`/api/users/${encodeURIComponent(id)}`, body);
}

export function resetUserPassword(id: string, password: string): Promise<void> {
  return api.post<void>(`/api/users/${encodeURIComponent(id)}/reset-password`, { password });
}

// T09: Paid advances derived
export function derivePaidAdvances(runId: string): Promise<Array<{ owner_profile_id: string; paid_total: string }>> {
  return api.post<Array<{ owner_profile_id: string; paid_total: string }>>(`/api/weg-settlements/${runId}/paid-advances-derived`);
}

export function getConsumptionSuggestions(runId: string): Promise<ConsumptionSuggestion[]> {
  return api.get<ConsumptionSuggestion[]>(`/api/settlements/${encodeURIComponent(runId)}/consumption-suggestions`);
}

export function listMeters(propertyId: string, unitId?: string): Promise<Meter[]> {
  const query = new URLSearchParams({ property_id: propertyId });
  if (unitId) query.set("unit_id", unitId);
  return api.get<Meter[]>(`/api/meters?${query}`);
}
export function createMeter(body: MeterCreate): Promise<Meter> {
  return api.post<Meter>("/api/meters", {
    property_id: body.property_id, unit_id: body.unit_id ?? null,
    meter_number: body.meter_number, meter_type: body.meter_type, location: body.location ?? "",
  });
}
export function updateMeter(id: string, body: MeterPatch): Promise<Meter> {
  return api.patch<Meter>(`/api/meters/${encodeURIComponent(id)}`, {
    meter_number: body.meter_number, meter_type: body.meter_type, location: body.location,
  });
}
export function listMeterReadings(id: string): Promise<MeterReading[]> {
  return api.get<MeterReading[]>(`/api/meters/${encodeURIComponent(id)}/readings`);
}
export function createMeterReading(id: string, body: MeterReadingCreate): Promise<MeterReading> {
  return api.post<MeterReading>(`/api/meters/${encodeURIComponent(id)}/readings`, {
    reading_date: body.reading_date, value: body.value, period: body.period ?? null,
  });
}

// Sonderumlagen: frozen allocations are always computed by the server.
export function listSpecialLevies(accountingScopeId: string, propertyId: string): Promise<SpecialLevy[]> {
  return api.get<SpecialLevy[]>(`/api/special-levies?accounting_scope_id=${encodeURIComponent(accountingScopeId)}&property_id=${encodeURIComponent(propertyId)}`);
}

export function createSpecialLevy(body: SpecialLevyCreate): Promise<SpecialLevy> {
  return api.post<SpecialLevy>("/api/special-levies", {
    property_id: body.property_id,
    accounting_scope_id: body.accounting_scope_id,
    amount: body.amount,
    due_date: body.due_date,
    ...(body.resolution_id !== undefined ? { resolution_id: body.resolution_id } : {}),
  });
}

// General document filing: retain originals, archive metadata only.
export function listAttachments(filters: AttachmentFilters): Promise<AttachmentPage> {
  const query = new URLSearchParams();
  for (const key of ["property_id", "unit_id", "lease_agreement_id", "person_id", "include_archived", "offset", "limit"] as const) {
    if (filters[key] !== undefined) query.set(key, String(filters[key]));
  }
  return api.get<AttachmentPage>(`/api/attachments?${query}`);
}
export function getAttachment(id: string): Promise<Attachment> {
  return api.get<Attachment>(`/api/attachments/${encodeURIComponent(id)}`);
}
export function uploadAttachment(file: File, body: AttachmentUpload): Promise<Attachment> {
  const data = new FormData();
  data.append("file", file);
  data.append("property_id", body.property_id);
  for (const key of ["title", "unit_id", "lease_agreement_id", "person_id"] as const) {
    if (body[key] !== undefined) data.append(key, body[key]);
  }
  return uploadFormData<Attachment>("/api/attachments", data);
}
export function archiveAttachment(id: string, reason: string): Promise<Attachment> {
  return api.post<Attachment>(`/api/attachments/${encodeURIComponent(id)}/archive`, { reason });
}
export function attachmentDownloadUrl(id: string, inline = false): string {
  return `/api/attachments/${encodeURIComponent(id)}/download${inline ? "?inline=true" : ""}`;
}

// --- Aufgaben: explicit allowlists keep server-owned metadata out of writes. ---
export function listTasks(filters: TaskFilters = {}): Promise<TaskPage> {
  const query = new URLSearchParams();
  for (const key of ["property_id", "unit_id", "lease_agreement_id", "assigned_user_id", "completed", "due_until", "offset", "limit"] as const) {
    const value = filters[key];
    if (value !== undefined) query.set(key, String(value));
  }
  return api.get(`/api/tasks${query.size ? `?${query}` : ""}`);
}
function taskBody(body: Partial<TaskInput>) {
  return Object.fromEntries((["title", "note", "due_date", "completed", "assigned_user_id", "property_id", "unit_id", "lease_agreement_id"] as const)
    .filter(key => body[key] !== undefined).map(key => [key, body[key]]));
}
export function getTask(id: string): Promise<Task> {
  return api.get(`/api/tasks/${encodeURIComponent(id)}`);
}
export function createTask(body: TaskInput): Promise<Task> {
  return api.post("/api/tasks", taskBody(body));
}
export function updateTask(id: string, body: Partial<TaskInput>): Promise<Task> {
  return api.patch(`/api/tasks/${encodeURIComponent(id)}`, taskBody(body));
}
export function deleteTask(id: string): Promise<void> {
  return api.delete(`/api/tasks/${encodeURIComponent(id)}`);
}
export async function listTaskAssignees(): Promise<User[]> {
  const users: User[] = [];
  let cursor: string | null = null;
  do {
    const query = new URLSearchParams({ eligible_for_scope: "true" });
    if (cursor) query.set("cursor", cursor);
    const page: { items: User[]; next_cursor: string | null } = await api.get(`/api/users/page?${query}`);
    users.push(...page.items);
    cursor = page.next_cursor;
  } while (cursor);
  return users;
}
export function getFristenContext(): Promise<{ requires_accounting_scope: boolean }> {
  return api.get("/api/fristen/context");
}
