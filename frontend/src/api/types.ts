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

export interface OrganizationQuota {
  plan: "free" | "unlimited";
  usage: { storage_bytes: number; properties: number; units: number; ai_budget_usd: string };
  limits: { storage_bytes: number | null; upload_bytes: number | null; properties: number | null; units: number | null; ai_budget_usd: string | null };
}

export interface Mandate {
  id: string;
  mandate_type: "WEG" | "MIET" | "SEV";
  label: string;
  start_date: string;
  end_date: string | null;
}

export interface MandateCreate {
  mandate_type: "WEG" | "MIET" | "SEV";
  label: string;
  start_date: string;
  end_date: string | null;
}

export type MandateUpdate = Partial<Pick<Mandate, "label" | "mandate_type" | "start_date" | "end_date">>;

export type MandateDocType =
  | "VERWALTERVERTRAG"
  | "BANKKONTO"
  | "VOLLMACHT"
  | "VERSICHERUNG"
  | "SONSTIGES";

export interface MandateDocument {
  id: string;
  management_mandate_id: string;
  doc_type: MandateDocType;
  title: string;
  original_filename: string | null;
  mime_type: string;
  content_byte_size: number;
  uploaded_at: string | null;
  uploaded_by: string | null;
  accounting_scope_id: string | null;
  scope_reason: string | null;
  archived_at: string | null;
  archive_reason: string | null;
}

export type UserRole = "OWNER_ADMIN" | "CLERK" | "READ_ONLY";

export interface User {
  id: string;
  email: string;
  display_name: string;
  is_active: boolean;
  role?: string | null;
}

// --- M1 Stammdaten ---

export type ManagementType = "MIET" | "WEG" | "SEV" | "MISCH";

export interface Property {
  construction_year?: number | null;
  total_area?: string | null;
  id: string;
  name: string;
  street: string;
  zip_code: string;
  city: string;
  management_type: ManagementType;
  fiscal_year_start_month: number;
  mea_denominator: number | null;
  mea_scale_status: MeaScaleStatus;
}

export type MeaScaleStatus = "LEGACY_UNCONFIRMED" | "CONFIRMED";

export interface PropertyCreate {
  construction_year?: number | null;
  total_area?: string | null;
  data_audience?: DeclarationApprovalRequest["new_property_audience"];
  name: string;
  street?: string;
  zip_code?: string;
  city?: string;
  management_type: ManagementType;
  fiscal_year_start_month?: number;
}

export interface PropertyUpdate {
  construction_year?: number | null;
  total_area?: string | null;
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
  building_part: string;
}

export interface UnitCreate {
  property_id: string;
  number: string;
  location?: string;
  area?: string | null;
  heated_area?: string | null;
  usage_type: UsageType;
  mea?: string | null;
  building_part?: string;
}

export interface UnitUpdate {
  number?: string;
  location?: string;
  area?: string | null;
  heated_area?: string | null;
  usage_type?: UsageType;
  mea?: string | null;
  building_part?: string | null;
}

// --- Teilungserklärungs-Import ---

export type DeclarationImportMode = "NEW_PROPERTY" | "EXISTING_PROPERTY";

export type DeclarationImportStatus =
  | "DRAFT"
  | "QUEUED"
  | "PREFLIGHT"
  | "EXTRACTING"
  | "RECONCILING"
  | "RETRY_WAIT"
  | "NEEDS_REVIEW"
  | "APPLYING"
  | "APPLIED"
  | "FAILED"
  | "CANCELLED";

export interface DeclarationImport {
  queue_request_id?: string | null;
  command_receipt_id?: string | null;
  id: string;
  mode: DeclarationImportMode;
  target_property_id: string | null;
  applied_property_id: string | null;
  status: DeclarationImportStatus;
  draft_revision: number;
  progress_percent: number;
  current_stage: string | null;
  error_code: string | null;
  error_message: string | null;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: string;
  attempt_count: number;
  max_cost_usd: string;
}

export type DeclarationImportCreate =
  | { mode: "NEW_PROPERTY"; target_property_id?: never }
  | { mode: "EXISTING_PROPERTY"; target_property_id: string };

export type DeclarationDocumentRole = "DECLARATION" | "AMENDMENT" | "ANNEX" | "OTHER";

export interface DeclarationDocument {
  id: string;
  property_id: string | null;
  doc_type: DeclarationDocumentRole;
  title: string;
  original_filename: string;
  mime_type: string;
  content_byte_size: number;
  content_sha256: string;
  sequence_no: number | null;
  document_role: DeclarationDocumentRole | null;
}

export interface DeclarationDocumentUpdate {
  title?: string;
  document_role?: DeclarationDocumentRole;
  sequence_no?: number;
}

export interface PropertyDocument {
  id: string;
  property_id: string;
  doc_type: DeclarationDocumentRole;
  title: string;
  original_filename: string;
  mime_type: string;
  content_byte_size: number;
  content_sha256: string;
  page_count: number;
}

// --- Gebäudewissen (freigegebene Teilungserklärungsdaten) ---

export interface KnowledgeSource {
  id: string;
  document_id: string;
  document_title: string;
  pdf_page: number;
  printed_page: string | null;
  section: string | null;
  quote: string;
}

export type KnowledgeStatus = "EFFECTIVE" | "SUPERSEDED" | "NEEDS_CLARIFICATION" | "RETRACTED";

export interface KnowledgeHit {
  id: string;
  kind: "FACT" | "RULE";
  topic: string;
  title: string;
  summary: string;
  status: KnowledgeStatus;
  scope: Record<string, unknown>;
  effect: Record<string, unknown>;
  sources: KnowledgeSource[];
  score: number;
}

export interface PropertyKnowledgeDocument {
  id: string;
  doc_type: string;
  title: string;
  document_date: string | null;
  deed_reference: string | null;
  page_count: number;
  archived_at: string | null;
}

export interface DocumentRelation {
  id: string;
  source_document_id: string;
  target_document_id: string;
  relation_type: string;
  status: string;
}

export interface RuleRelation {
  id: string;
  source_rule_id: string;
  target_rule_id: string;
  relation_type: string;
}

export interface PropertyRule extends KnowledgeHit {
  relations: RuleRelation[];
}

export interface PropertyKnowledgeOverview {
  property_id: string;
  documents: PropertyKnowledgeDocument[];
  document_relations: DocumentRelation[];
  rules_by_topic: Record<string, PropertyRule[]>;
  open_rule_topics: string[];
}

export interface KnowledgeSearchResponse {
  property_id: string;
  normalized_query: string;
  limit: number;
  include_history: boolean;
  hits: KnowledgeHit[];
}

export interface KnowledgeQuestionRequest {
  question: string;
}

export interface KnowledgeAnswerOut {
  status: "ANSWER" | "NOT_DOCUMENTED" | "NEEDS_CLARIFICATION";
  answer: string;
  fact_ids: string[];
  rule_ids: string[];
  source_ids: string[];
  sources: KnowledgeSource[];
}

export type DeclarationCandidateType =
  | "PROPERTY_FIELD"
  | "LEGAL_REFERENCE"
  | "UNIT_CREATE"
  | "UNIT_FIELD"
  | "MEA_RESCALE"
  | "ASSIGNMENT"
  | "RULE"
  | "DOCUMENT_RELATION";

export type DeclarationCandidateOperation = "CREATE" | "UPDATE" | "KEEP";
export type CandidateRiskClass = "LOW" | "MEDIUM" | "CRITICAL";
export type CandidateDecision =
  | "ACCEPTED"
  | "CORRECTED"
  | "REJECTED"
  | "NEEDS_CLARIFICATION";

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type NonNullJsonValue = Exclude<JsonValue, null>;

export interface CandidateEvidence {
  id: string;
  document_id: string;
  field_path: string | null;
  pdf_page: number;
  printed_page: string | null;
  section_ref: string | null;
  quote: string;
}

export interface CandidateIssue {
  id: string;
  code: string;
  severity: string;
  message: string;
  resolution_status: string;
  candidate_id: string | null;
}

export interface DeclarationCandidate {
  id: string;
  stable_key: string;
  candidate_type: DeclarationCandidateType;
  subject_key: string;
  field_key: string | null;
  operation: DeclarationCandidateOperation;
  previous_value_json: Record<string, unknown> | null;
  proposed_value_json: Record<string, unknown> | null;
  corrected_value_json: Record<string, unknown> | null;
  confidence: string;
  risk_class: CandidateRiskClass;
  requires_individual_review: boolean;
  review_status: CandidateDecision | "PENDING";
  review_comment: string | null;
  evidence: CandidateEvidence[];
  issues: CandidateIssue[];
}

export type CandidateReviewRequest =
  | {
      draft_revision: number;
      decision: "CORRECTED";
      corrected_value: NonNullJsonValue;
      comment?: string;
    }
  | {
      draft_revision: number;
      decision: "ACCEPTED" | "REJECTED";
      corrected_value?: never;
      comment?: string;
    }
  | {
      draft_revision: number;
      decision: "NEEDS_CLARIFICATION";
      corrected_value?: never;
      comment: string;
    };

export interface CandidateReviewResult {
  draft_revision: number;
  candidate: DeclarationCandidate | null;
  candidates: DeclarationCandidate[];
}

export interface BulkCandidateReviewRequest {
  draft_revision: number;
  candidate_ids: string[];
  decision: "ACCEPTED" | "REJECTED";
  comment?: string;
}

export interface ApprovalPreview {
  import_id: string;
  draft_revision: number;
  can_approve: boolean;
  issues: CandidateIssue[];
  pending_candidate_ids: string[];
  import_mode: "NEW_PROPERTY" | "EXISTING_PROPERTY";
}

export type ApprovalSummary =
  | {
      properties_created: 1;
      properties_updated?: never;
      units_created: number;
      units_updated: 0;
      rules_created: number;
      documents_bound: number;
    }
  | {
      properties_created: 0;
      properties_updated: 0 | 1;
      units_created: number;
      units_updated: number;
      rules_created: number;
      documents_bound: number;
    };

export interface ApprovalResult {
  import_id: string;
  status: "APPLIED";
  property_id: string;
  draft_revision: number;
  summary: ApprovalSummary;
}

export interface DeclarationApprovalRequest {
  draft_revision: number;
  idempotency_key: string;
  new_property_audience?: {
    label: string;
    reason: string;
    grants: { user_id: string; capabilities: ("read" | "prepare" | "approve" | "export")[]; reason: string; expires_at?: string | null }[];
  };
}

export interface Person {
  id: string;
  can_prepare: boolean;
  can_export: boolean;
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
  is_active?: boolean;
  id: string;
  person_id: string;
  tax_id: string | null;
  vat_id: string | null;
  notes: string;
}

export type VendorUpdate = Partial<Pick<Vendor, "tax_id" | "vat_id" | "notes" | "is_active">>;

export interface OwnershipRecord {
  id: string;
  unit_id: string;
  owner_profile_id: string;
  valid_from: string;
  valid_to: string | null;
  mea_share: string | null;
}

export interface OwnershipEndRequest {
  valid_to: string;
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
  heating_advance_payment: string;
  deposit: string | null;
  notes: string;
}

export interface LeaseRentChange {
  id: string;
  lease_agreement_id: string;
  valid_from: string;
  base_rent: string;
  advance_payment: string;
  heating_advance_payment: string;
  created_at: string;
}

export interface LeaseRentChangeCreate {
  valid_from: string;
  base_rent: string;
  advance_payment: string;
  heating_advance_payment: string;
}

export interface LeaseCreate {
  unit_id: string;
  tenant_profile_id: string;
  valid_from: string;
  valid_to?: string | null;
  base_rent: string;
  advance_payment: string;
  heating_advance_payment: string;
  deposit?: string | null;
  notes?: string;
}

export interface LeaseUpdate {
  valid_to?: string | null;
  notes?: string;
  base_rent?: string;
  advance_payment?: string;
  heating_advance_payment?: string;
  deposit?: string | null;
}

// --- M2 Kosten & Belege ---

export type CostEntryStatus =
  | "draft_extracted"
  | "needs_review"
  | "confirmed"
  | "rejected"
  | "superseded";

export interface CostCategory {
  id: string;
  plan_version: number;
  code: string;
  label: string;
  cost_group: string;
  tenant_apportionability_default: string;
  owner_billing_relevant_default: boolean;
  owner_distribution_key_default: string | null;
  tenant_distribution_key_default: string | null;
  reserve_relevant: boolean;
  legal_hint: string | null;
  sort_order: number;
  is_active: boolean;
}

export interface CostEntry {
  id: string;
  organization_id: string;
  property_id: string;
  management_mandate_id: string | null;
  cost_category_id: string;
  cost_category_plan_version: number;
  vendor_id: string | null;
  invoice_date: string;
  service_period_from: string;
  service_period_to: string;
  gross_amount: string;
  vat_amount: string | null;
  vat_rate: string | null;
  document_number: string | null;
  owner_distribution_key: string | null;
  tenant_distribution_key: string | null;
  tenant_apportionable: string;
  apportionable_tenant_amount: string | null;
  status: CostEntryStatus;
  source: string;
  confirmed_at: string | null;
  confirmed_by: string | null;
  superseded_by_id: string | null;
}

export interface CostEntryCreate {
  accounting_scope_id?: string | null;
  source_document_id?: string;
  property_id: string;
  management_mandate_id?: string | null;
  cost_category_id: string;
  cost_category_plan_version?: number;
  vendor_id?: string | null;
  invoice_date: string;
  service_period_from: string;
  service_period_to: string;
  gross_amount: string;
  vat_amount?: string | null;
  vat_rate?: string | null;
  document_number?: string | null;
  owner_distribution_key?: string | null;
  tenant_distribution_key?: string | null;
  tenant_apportionable: string;
  apportionable_tenant_amount?: string | null;
  source?: string;
  status?: string;
}

export interface CostEntryUpdate {
  gross_amount?: string | null;
  vat_amount?: string | null;
  vat_rate?: string | null;
  invoice_date?: string | null;
  service_period_from?: string | null;
  service_period_to?: string | null;
  cost_category_id?: string | null;
  cost_category_plan_version?: number | null;
  vendor_id?: string | null;
  document_number?: string | null;
  owner_distribution_key?: string | null;
  tenant_distribution_key?: string | null;
  tenant_apportionable?: string | null;
  apportionable_tenant_amount?: string | null;
}

export interface CostEntryCorrectionCreate {
  gross_amount?: string | null;
  vat_amount?: string | null;
  vat_rate?: string | null;
  invoice_date?: string | null;
  service_period_from?: string | null;
  service_period_to?: string | null;
  cost_category_id?: string | null;
  cost_category_plan_version?: number | null;
  vendor_id?: string | null;
  document_number?: string | null;
  owner_distribution_key?: string | null;
  tenant_distribution_key?: string | null;
  tenant_apportionable?: string | null;
  apportionable_tenant_amount?: string | null;
}

export interface ExtractionPayload {
  vendor_name: string | null;
  invoice_date: string | null;
  document_number: string | null;
  gross_amount: string | null;
  vat_amount: string | null;
  vat_rate: string | null;
  service_period_from: string | null;
  service_period_to: string | null;
  suggested_category_code: string | null;
  confidence: number;
  field_confidences: Record<string, number>;
  raw_text: string | null;
  /** M5: true when parsed from structured EN-16931 (XRechnung/ZUGFeRD). */
  is_einvoice?: boolean;
  /** M5: concrete format, e.g. "XRECHNUNG_UBL", "XRECHNUNG_CII", "ZUGFERD". */
  source_format?: string | null;
}

export interface InvoiceDocument {
  processing_status: string;
  processing_error_code: string | null;
  id: string;
  organization_id: string;
  cost_entry_id: string | null;
  storage_backend: string;
  original_filename: string | null;
  mime_type: string;
  content_byte_size: number;
  content_sha256: string;
  version: number;
  supersedes_id: string | null;
  chain_root_id: string;
  status: string;
  released_at: string | null;
  released_by: string | null;
  retention_until: string | null;
  superseded_at: string | null;
  extraction_payload: ExtractionPayload | null;
}

export interface VerifyResult {
  document_id: string;
  integrity_ok: boolean;
  content_sha256: string;
}

// --- M3 Settlements ---

export type SettlementStatus = "draft" | "finalized";

export interface SettlementLine {
  id: string;
  cost_entry_id: string | null;
  line_type: string;
  cost_category_code: string | null;
  label: string;
  distribution_key: string | null;
  total_amount: string | null;
  distribution_base_total: string | null;
  distribution_base_share: string | null;
  time_share_days: number | null;
  time_share_total_days: number | null;
  amount: string;
  sort_order: number;
  note: string | null;
}

export interface SettlementRun {
  id: string;
  organization_id: string;
  property_id: string;
  management_mandate_id: string | null;
  run_type: string;
  period_start: string;
  period_end: string;
  unit_id: string | null;
  lease_agreement_id: string | null;
  tenant_profile_id: string | null;
  advance_payments_total: string | null;
  advance_payments_source: string;
  heating_advance_payments_total: string | null;
  heating_advance_payments_source: string;
  total_cost: string | null;
  balance: string | null;
  status: SettlementStatus;
  snapshot_sha256: string | null;
  engine_version: string;
  finalized_at: string | null;
  finalized_by: string | null;
  superseded_by_id: string | null;
  lines: SettlementLine[];
  extra_inputs?: ComputeRequest | null;
}

export interface AdvanceProposal {
  accept_from: string;
  heating_tenant_cost: string;
  heating_monthly_amount: string;
  current_monthly_amount: string;
  current_heating_monthly_amount: string;
  lease_agreement_id: string;
  tenant_cost: string;
  months: string;
  monthly_amount: string;
  period_start: string;
  period_end: string;
}

export interface AdvanceProposalApply {
  valid_from: string;
}

export interface SettlementRunCreate {
  accounting_scope_id?: string | null;
  property_id: string;
  lease_agreement_id: string;
  period_start: string;
  period_end: string;
  advance_payments_total?: string | null;
  heating_advance_payments_total?: string | null;
  management_mandate_id?: string | null;
}

export interface SettlementBatchCreate {
  property_id: string;
  period_start: string;
  period_end: string;
  accounting_scope_id?: string | null;
}

export interface SettlementBatchResult {
  created: SettlementRun[];
  skipped: { lease_agreement_id: string; settlement_run_id: string }[];
  errors?: { lease_agreement_id: string; code: string; error: string }[];
}

export interface AdvancePaymentsUpdate {
  advance_payments_total?: string;
  heating_advance_payments_total?: string;
}

export interface ExternalAmountIn {
  cost_entry_id: string;
  unit_id: string;
  tenant_amount: string;
  period_from?: string | null;
  period_to?: string | null;
}

export interface ManualAmountIn {
  cost_entry_id: string;
  unit_id: string;
  amount: string;
}

export interface UnitMetricIn {
  unit_id: string;
  consumption?: string | null;
  person_count?: number | null;
}

export type HeatingBaseKey = "AREA" | "HEATED_AREA" | "VOLUME";

export interface HeatingSlice {
  unit_id: string;
  period_from: string;
  period_to: string;
  consumption?: string;
  base_quantity?: string;
}

export interface HeatingConfig {
  cost_entry_id: string;
  kind: "HEATING" | "HOT_WATER";
  consumption_fraction: string;
  base_key: HeatingBaseKey;
  consumption_recorded: boolean;
  mandatory_seventy_percent: boolean;
  meter_type: string;
  slices: HeatingSlice[];
}

export interface HeatingConsumptionCandidate {
  unit_id: string;
  unit_label: string;
  period_from: string;
  period_to: string;
  base_quantity: string | null;
  consumption: string | null;
  meter_id: string | null;
  reason: string | null;
}

export interface ComputeRequest {
  external_amounts: ExternalAmountIn[];
  manual_amounts: ManualAmountIn[];
  unit_metrics: UnitMetricIn[];
  heating_inputs?: HeatingConfig[];
}

// --- M4 WEG: Wirtschaftsplan (Economic Plan) ---

export interface EconomicPlanLine {
  id: string;
  line_type: string;
  cost_category_code: string | null;
  owner_profile_id: string | null;
  unit_id: string | null;
  label: string;
  distribution_key: string | null;
  distribution_base_total: string | null;
  distribution_base_share: string | null;
  planned_amount: string;
  monthly_amount: string | null;
  cost_group: string | null;
  sort_order: number;
  note: string | null;
}

export interface EconomicPlan {
  id: string;
  organization_id: string;
  property_id: string;
  management_mandate_id: string | null;
  plan_year: number;
  period_start: string;
  period_end: string;
  monthly_due_day: number | null;
  reserve_contribution_total: string;
  status: SettlementStatus;
  snapshot_sha256: string | null;
  engine_version: string;
  finalized_at: string | null;
  finalized_by: string | null;
  superseded_by_id: string | null;
  lines: EconomicPlanLine[];
  draft_inputs?: {
    planned_costs: PlannedCostIn[];
    reserve_contribution_total: string;
  } | null;
}

export interface EconomicPlanCreate {
  accounting_scope_id?: string | null;
  property_id: string;
  plan_year: number;
  period_start: string;
  period_end: string;
  reserve_contribution_total?: string | null;
  monthly_due_day?: number | null;
  management_mandate_id?: string | null;
}

export interface PlannedCostIn {
  category_code: string;
  label: string;
  planned_amount: string;
  distribution_key?: string;
  category_id?: string | null;
  cost_group?: string;
}

export interface PlanComputeRequest {
  planned_costs: PlannedCostIn[];
  reserve_contribution_total: string;
}

// --- M6 Bank Accounts ---

export type BankAccountType = "LAUFEND" | "RUECKLAGE" | "KAUTION";

export interface BankAccount {
  is_active?: boolean;
  property_name?: string | null;
  id: string;
  organization_id: string;
  property_id: string;
  account_type: BankAccountType;
  label: string;
  iban: string | null;
  bic: string | null;
  bank_name: string | null;
}

export type BankAccountUpdate = Partial<Pick<BankAccount, "label" | "iban" | "bic" | "bank_name" | "is_active">>;

export interface BankAccountCreate {
  accounting_scope_id?: string | null;
  property_id: string;
  account_type: BankAccountType;
  label: string;
  iban?: string | null;
  bic?: string | null;
  bank_name?: string | null;
}

export type BankTransactionStatus = "NEU" | "ZUGEORDNET" | "IGNORIERT";

export interface BankTransaction {
  id: string;
  bank_account_id: string;
  booking_date: string;
  value_date: string;
  amount: string;
  counterparty_name: string | null;
  counterparty_iban: string | null;
  purpose: string | null;
  status: BankTransactionStatus;
}

export interface BankTransactionPage {
  items: BankTransaction[];
  next_offset: number | null;
}

export interface BankAssignmentLine {
  payment_type: PaymentType | "NACHZAHLUNG";
  amount: string;
  period_year: number;
  period_month: number;
  lease_agreement_id?: string;
  tenant_profile_id?: string;
  owner_profile_id?: string;
  note?: string;
  reserve_cost_entry_id?: string | null;
  reserve_register_entry_id?: string | null;
}

export interface BankMatchLine extends Omit<BankAssignmentLine, "amount" | "lease_agreement_id" | "tenant_profile_id" | "owner_profile_id"> {
  amount: string | null;
  open_amount: string;
  lease_agreement_id?: string | null;
  tenant_profile_id?: string | null;
  owner_profile_id?: string | null;
}

export interface BankMatchSuggestion {
  target_name: string;
  confidence: "HIGH" | "MEDIUM" | "LOW";
  lines: BankMatchLine[];
  reasons: string[];
  requires_review: boolean;
  unallocated_amount: string;
}

export interface BankTransactionWithSuggestions extends BankTransaction {
  suggestions: BankMatchSuggestion[];
}

export interface BankMatchingPage {
  items: BankTransactionWithSuggestions[];
  next_offset: number | null;
}

export interface BankAssignmentResult {
  id: string;
  status: "ZUGEORDNET";
  payments: (Payment & { bank_transaction_id: string })[];
}

export interface BankImportResult {
  count: number;
  new_count: number;
  duplicate_count: number;
  imported_count: number;
  total_amount: string;
  new_total_amount: string;
  date_from: string | null;
  date_to: string | null;
  transactions: (Omit<BankTransaction, "id" | "bank_account_id" | "status"> & {
    import_hash: string;
    is_duplicate: boolean;
  })[];
  preview_truncated: boolean;
}

export interface DepositAccount {
  lease_agreement_id: string;
  tenant_profile_id: string;
  unit_id: string;
  property_id: string;
  tenant_name: string;
  unit_label: string;
  property_name: string;
  expected_amount: string;
  paid_amount: string;
  refunded_amount: string;
  held_amount: string;
}

// --- M6 Payments ---

export type PaymentDirection = "IN" | "OUT";

export type PaymentType =
  | "KALTMIETE"
  | "NK_VORAUSZAHLUNG"
  | "HK_VORAUSZAHLUNG"
  | "KAUTION"
  | "HAUSGELD_VORSCHUSS"
  | "RUECKLAGENANTEIL"
  | "RUECKLAGENENTNAHME"
  | "SONDERZAHLUNG";

export type PaymentStatus = "draft" | "booked" | "cancelled";

export interface Payment {
  bank_transaction_id?: string | null;
  id: string;
  organization_id: string;
  bank_account_id: string;
  property_id: string;
  management_mandate_id: string | null;
  direction: PaymentDirection;
  amount: string;
  value_date: string;
  payment_type: string;
  tenant_profile_id: string | null;
  owner_profile_id: string | null;
  lease_agreement_id: string | null;
  period_year: number | null;
  period_month: number | null;
  settlement_run_id: string | null;
  status: PaymentStatus;
  booked_at: string | null;
  booked_by: string | null;
  reverses_id: string | null;
  reversed_by_id: string | null;
  retention_until: string | null;
  note: string | null;
  reserve_cost_entry_id?: string | null;
  reserve_register_entry_id?: string | null;
}

export interface PaymentCreate {
  bank_account_id: string;
  direction: PaymentDirection;
  amount: string;
  value_date: string;
  payment_type: string;
  tenant_profile_id?: string | null;
  owner_profile_id?: string | null;
  lease_agreement_id?: string | null;
  period_year?: number | null;
  period_month?: number | null;
  settlement_run_id?: string | null;
  note?: string | null;
  reserve_cost_entry_id?: string | null;
  reserve_register_entry_id?: string | null;
}

export interface PaymentUpdate {
  bank_account_id?: string | null;
  direction?: PaymentDirection | null;
  amount?: string | null;
  value_date?: string | null;
  payment_type?: string | null;
  tenant_profile_id?: string | null;
  owner_profile_id?: string | null;
  lease_agreement_id?: string | null;
  period_year?: number | null;
  period_month?: number | null;
  settlement_run_id?: string | null;
  note?: string | null;
  reserve_cost_entry_id?: string | null;
  reserve_register_entry_id?: string | null;
}

export interface OpenItem {
  period_year: number | null;
  period_month: number | null;
  payment_type: string;
  soll: string;
  ist: string;
  open_amount: string;
}

export interface MietkontoResult {
  open_items: OpenItem[];
  soll_total: string;
  ist_total: string;
  balance: string;
}

// --- M7 Dunning (Mahnwesen) ---

export type DunningStatus = "draft" | "issued" | "cancelled";

export interface DunningNotice {
  accounting_scope_id?: string | null;
  id: string;
  organization_id: string;
  tenant_profile_id: string | null;
  owner_profile_id: string | null;
  level: number;
  issue_date: string;
  due_date: string;
  principal_amount: string;
  fee_amount: string;
  interest_amount: string;
  interest_snapshot: DunningInterestSnapshot | null;
  total_amount: string;
  included_items: DunningIncludedItem[] | null;
  status: DunningStatus;
  issued_at: string | null;
  issued_by: string | null;
  note: string | null;
}

export interface DunningSettings {
  base_interest_rate: string | null;
}

export interface DunningInterestSnapshot {
  base_interest_rate: string;
  annual_rate: string;
  reference_date: string;
  items: {
    payment_type: string;
    period_year: number | null;
    period_month: number | null;
    due_date: string;
    days: number;
    principal: string;
    interest: string;
  }[];
}

export interface DunningIncludedItem {
  period_year: number | null;
  period_month: number | null;
  payment_type: string;
  soll: string;
  ist: string;
  open_amount: string;
}

export interface DunningCreateTenant {
  base_interest_rate?: string | null;
  accounting_scope_id?: string | null;
  tenant_profile_id: string;
  lease_agreement_id: string;
  period_start: string;
  period_end: string;
  reference_date?: string | null;
  grace_days?: number;
  override_fee?: string | null;
}

export interface DunningCreateOwner {
  base_interest_rate?: string | null;
  accounting_scope_id?: string | null;
  owner_profile_id: string;
  period_start: string;
  period_end: string;
  economic_plan_id?: string | null;
  reference_date?: string | null;
  grace_days?: number;
  override_fee?: string | null;
}

// --- M4 WEG: Jahresabrechnung + Vermoegensbericht (settlement_run run_types) ---

export interface WegLine {
  id: string;
  line_type: string;
  addressee_owner_profile_id: string | null;
  cost_category_code: string | null;
  label: string;
  distribution_key: string | null;
  total_amount: string | null;
  distribution_base_total: string | null;
  distribution_base_share: string | null;
  time_share_days: number | null;
  time_share_total_days: number | null;
  amount: string;
  sort_order: number;
  note: string | null;
}

export interface WegRun {
  id: string;
  organization_id: string;
  property_id: string;
  run_type: string;
  period_start: string;
  period_end: string;
  economic_plan_id: string | null;
  total_cost: string | null;
  balance: string | null;
  status: SettlementStatus;
  snapshot_sha256: string | null;
  engine_version: string;
  finalized_at: string | null;
  superseded_by_id: string | null;
  lines: WegLine[];
}

export interface WegRunCreate {
  accounting_scope_id?: string | null;
  property_id: string;
  economic_plan_id: string;
  period_start: string;
  period_end: string;
  management_mandate_id?: string | null;
}

export interface PaidAdvanceIn {
  owner_profile_id: string;
  paid_total: string;
}

export interface WegComputeRequest {
  reserve_opening_balance?: string | null;
  reserve_withdrawals_total?: string | null;
  reserve_override_reason?: string;
  paid_advances: PaidAdvanceIn[];
}

export interface AssetReportCreate {
  accounting_scope_id?: string | null;
  property_id: string;
  period_start: string;
  period_end: string;
  management_mandate_id?: string | null;
}

export interface AssetItemIn {
  label: string;
  amount: string;
}

export interface AssetReportComputeRequest {
  reserve_opening_balance?: string | null;
  reserve_withdrawals_total?: string | null;
  reserve_contributions_total?: string | null;
  reserve_override_reason?: string;
  bank_balances: { bank_account_id: string; label: string; account_type: BankAccountType; balance: string }[];
  receivables: AssetItemIn[];
  payables: AssetItemIn[];
}

// --- M8 Meetings & Beschluss-Sammlung ---
export type ReserveComponent = 'OPENING' | 'CONTRIBUTION' | 'WITHDRAWAL' | 'ADJUSTMENT' | 'FUNDING';
export interface ReserveOverride {
  unit_id?: string;
  component: ReserveComponent;
  amount: string;
  reason: string;
  payment_id?: string;
  cost_entry_id?: string;
  register_entry_id?: string;
}
export interface ReserveLedger {
  contribution_headroom?: { unit_id: string; unit_number: string; remaining: string }[];
  unlinked_payment_ids?: string[];
  opening_balance: string;
  contributions_total: string;
  withdrawals_total: string;
  overrides_total: string;
  late_cash_total?: string;
  closing_balance: string;
  planned_contributions_total: string | null;
  overdrawn: boolean;
  frozen: boolean;
  booking_basis: string;
  entries: { kind: string; component?: ReserveComponent; date: string; amount: string; reason?: string;
    payment_note?: string | null; cost_document_number?: string | null; register_sequence_number?: number | null; owner_name?: string | null;
    id?: string; payment_id?: string; cost_entry_id?: string; register_entry_id?: string; unit_id?: string; unit_number?: string; owner_profile_id?: string }[];
}


export type MeetingStatus = "PLANNED" | "HELD";
export type MeetingType = "ORDENTLICH" | "AUSSERORDENTLICH";
export type ResolutionResult = "ANGENOMMEN" | "ABGELEHNT";

export interface Meeting {
  id: string;
  organization_id: string;
  property_id: string;
  meeting_date: string;
  location: string;
  meeting_type: string;
  status: MeetingStatus;
  invitation_text: string | null;
  protocol_text: string | null;
  invitation_short_notice?: boolean;
}

export interface MeetingCreate {
  property_id: string;
  meeting_date: string;
  meeting_type: string;
  location?: string;
  invitation_text?: string | null;
}

export interface MeetingUpdate {
  meeting_date?: string | null;
  location?: string | null;
  meeting_type?: string | null;
  invitation_text?: string | null;
  protocol_text?: string | null;
}

export interface AgendaItem {
  id: string;
  organization_id: string;
  meeting_id: string;
  item_number: number;
  title: string;
  description: string | null;
}

export interface AgendaItemCreate {
  meeting_id: string;
  title: string;
  description?: string | null;
}

export interface AgendaItemUpdate {
  title?: string | null;
  description?: string | null;
}

export interface Resolution {
  id: string;
  organization_id: string;
  meeting_id: string;
  agenda_item_id: string | null;
  wording: string;
  votes_yes: number;
  votes_no: number;
  votes_abstain: number;
  votes_yes_mea: string | null;
  votes_no_mea: string | null;
  votes_abstain_mea: string | null;
  result: string;
  majority_type: string | null;
}

export interface ResolutionCreate {
  meeting_id: string;
  wording: string;
  votes_yes: number;
  votes_no: number;
  votes_abstain: number;
  result: string;
  agenda_item_id?: string | null;
  votes_yes_mea?: string | null;
  votes_no_mea?: string | null;
  votes_abstain_mea?: string | null;
  majority_type?: string | null;
}

export interface RegisterEntry {
  id: string;
  organization_id: string;
  property_id: string;
  sequence_number: number;
  entry_date: string;
  location: string;
  wording: string;
  resolution_id: string;
  result: string | null;
  challenge_annotation: string | null;
}

export interface ChallengeAnnotation {
  annotation: string | null;
}

// --- M9 Fristen ---
export type DeadlineStatus =
  | "OVERDUE"
  | "DUE"
  | "UPCOMING"
  | "INFO"
  | "OK";

export interface DeadlineItem {
  kind: string;
  reference: string;
  due_date: string | null;
  status: DeadlineStatus;
  is_statutory: boolean;
  is_provisional: boolean;
  property_id: string | null;
  detail: string;
}

// --- M9 DSGVO ---
export interface DsgvoBinding {
  kind: string;
  entity_type: string;
  entity_id: string;
  retention_until: string | null;
}

export interface DsgvoStatus {
  person_id: string;
  applied: string;
  already_anonymized: boolean;
  anonymized_at: string | null;
  processing_restricted: boolean;
  deletion_requested_at: string | null;
  has_active_relationship: boolean;
  erasable: boolean;
  decision: "RESTRICT" | "ANONYMIZE_NOW";
  is_bound: boolean;
  purgeable_now: boolean;
  governing_retention_until: string | null;
  bindings: DsgvoBinding[];
}

export interface DsgvoExport {
  person: Record<string, string | null>;
  roles: {
    vendor_ids: string[];
    tenant_profile_ids: string[];
    owner_profile_ids: string[];
  };
  processing: {
    anonymized_at: string | null;
    processing_restricted: boolean;
    deletion_requested_at: string | null;
  };
  retention_bindings: DsgvoBinding[];
}


export interface SourceAdmissionReviewCommand {
  document_id: string;
  import_id: string;
  draft_revision: number;
  content_sha256: string;
  reason: string;
}

export interface PendingSourceAdmissionReview extends Omit<SourceAdmissionReviewCommand, "reason"> {
  id: string;
  property_id: string;
  property_name: string;
  title: string;
  original_filename: string;
  can_preview: boolean;
}

export interface SourceAdmissionReviewReceipt extends SourceAdmissionReviewCommand {
  id: string;
  property_id: string;
  domain_id: string;
  reviewed_by: string;
  created_at: string;
}

export interface SourceClassificationCommand {
  document_id: string;
  property_id: string;
  content_sha256: string;
  source_context_digest: string;
  decision: "ADMITTED" | "QUARANTINED";
  reason: string;
}
export interface PendingSourceClassification extends Omit<SourceClassificationCommand, "reason" | "decision"> {
  id: string;
  property_name: string;
  source_uploaded_by: string | null;
  linked_import_count: number;
  can_admit: boolean;
  can_preview: boolean;
  quiescent: boolean;
}
export interface SourceClassificationReceipt extends SourceClassificationCommand {
  id: string;
  domain_id: string;
  reviewed_by: string;
  created_at: string;
}

export interface AccessPreparation {
  generated_at: string;
  mode: "PREPARATION" | "ENFORCED" | "PAUSED";
  activation_available: false;
  counts: {
    properties_total: number;
    properties_without_domain: number;
    properties_without_current_reader: number;
    contacts_total: number;
    contacts_without_domain: number;
    contacts_unknown_creator: number;
    contacts_without_current_reader: number;
    originals_total: number;
    bound_originals_without_review_record: number;
    originals_quarantined: number;
    unbound_originals_unknown_uploader: number;
    bank_accounts_without_scope: number;
    payments_without_scope: number;
    cost_entries_without_scope: number;
    settlements_without_scope: number;
    economic_plans_without_scope: number;
    dunning_without_context: number;
    mandate_originals_without_scope: number;
  };
}

export interface Meter {
  id: string;
  organization_id: string;
  property_id: string;
  unit_id: string | null;
  meter_number: string;
  meter_type: string;
  location: string;
}
export interface MeterCreate {
  property_id: string;
  unit_id?: string | null;
  meter_number: string;
  meter_type: string;
  location?: string;
}
export interface MeterPatch {
  meter_number?: string;
  meter_type?: string;
  location?: string;
}
export interface MeterReading {
  id: string;
  meter_id: string;
  reading_date: string;
  value: string;
  period: string | null;
}
export interface MeterReadingCreate {
  reading_date: string;
  value: string;
  period?: string | null;
}

export interface ConsumptionReading {
  id: string;
  reading_date: string;
  value: string;
}
export interface ConsumptionSuggestion {
  unit_id: string;
  unit_label: string;
  consumption: string | null;
  meter_id: string | null;
  start_reading: ConsumptionReading | null;
  end_reading: ConsumptionReading | null;
  reason: string | null;
}

export interface PropertyYearReportQuery {
  property_id: string;
  year: number;
  accounting_scope_id?: string;
}

export interface PropertyYearReport {
  property_id: string;
  property_name: string;
  year: number;
  period_start: string;
  period_end: string;
  accounting_scope_id: string | null;
  income_date_basis: "value_date";
  expense_date_basis: "invoice_date";
  income: { payment_type: string; amount: string }[];
  expenses: {
    category_id: string;
    category_code: string;
    category_name: string;
    tenant_apportionability: string;
    amount: string;
  }[];
  income_total: string;
  expense_total: string;
  balance: string;
  footnote: string;
}

export interface AnlageVSummary {
  property_id: string;
  property_name: string;
  year: number;
  accounting_scope_id: string | null;
  lines: { key: string; label: string; section: "Einnahmen" | "Werbungskosten"; amount: string; note: string }[];
  unmapped: { code: string; label: string; section: "Einnahmen" | "Werbungskosten"; amount: string; note: string }[];
  reconciliation: { section: "Einnahmen" | "Werbungskosten"; mapped_total: string; unmapped_total: string; report_total: string; difference: string }[];
  footnote: string;
}

export interface SpecialLevyAllocation {
  owner_profile_id: string;
  unit_id: string;
  mea: string;
  amount: string;
}

export interface SpecialLevyCreate {
  property_id: string;
  accounting_scope_id: string;
  amount: string;
  due_date: string;
  resolution_id?: string | null;
}

export interface SpecialLevy extends SpecialLevyCreate {
  id: string;
  resolution_id: string | null;
  allocations: SpecialLevyAllocation[];
}

// Retained originals; no storage locations are exposed by this API.
export interface Attachment {
  id: string;
  property_id: string;
  unit_id: string | null;
  lease_agreement_id: string | null;
  person_id: string | null;
  title: string;
  original_filename: string;
  mime_type: string;
  content_byte_size: number;
  content_sha256: string;
  uploaded_by: string;
  created_at: string;
  archived_at: string | null;
  archived_by: string | null;
  archive_reason: string | null;
}
export interface AttachmentPage {
  items: Attachment[];
  next_offset: number | null;
}
export interface AttachmentFilters {
  property_id?: string;
  unit_id?: string;
  lease_agreement_id?: string;
  person_id?: string;
  include_archived?: boolean;
  offset?: number;
  limit?: number;
}
export interface AttachmentUpload {
  property_id: string;
  title?: string;
  unit_id?: string;
  lease_agreement_id?: string;
  person_id?: string;
}

// --- Aufgaben ---
export interface Task {
  id: string;
  organization_id: string;
  created_by: string;
  assigned_user_id: string;
  property_id: string | null;
  unit_id: string | null;
  lease_agreement_id: string | null;
  title: string;
  note: string;
  due_date: string | null;
  completed: boolean;
  created_at: string;
  updated_at: string;
  property_name: string | null;
  unit_number: string | null;
  lease_label: string | null;
  assigned_user_name: string | null;
}
export interface TaskInput {
  title: string;
  note?: string;
  due_date?: string | null;
  completed?: boolean;
  assigned_user_id?: string;
  property_id?: string | null;
  unit_id?: string | null;
  lease_agreement_id?: string | null;
}
export interface TaskFilters {
  property_id?: string;
  unit_id?: string;
  lease_agreement_id?: string;
  assigned_user_id?: string;
  completed?: boolean;
  due_until?: string;
  offset?: number;
  limit?: number;
}
export interface TaskPage { items: Task[]; next_offset: number | null }

export interface SettlementPeriod {
  year: number;
  label: string;
  period_start: string;
  period_end: string;
}
