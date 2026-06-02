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
}

export interface InvoiceDocument {
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
  total_cost: string | null;
  balance: string | null;
  status: SettlementStatus;
  snapshot_sha256: string | null;
  engine_version: string;
  finalized_at: string | null;
  finalized_by: string | null;
  superseded_by_id: string | null;
  lines: SettlementLine[];
}

export interface SettlementRunCreate {
  property_id: string;
  lease_agreement_id: string;
  period_start: string;
  period_end: string;
  advance_payments_total?: string | null;
  management_mandate_id?: string | null;
}

export interface AdvancePaymentsUpdate {
  advance_payments_total: string;
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

export interface ComputeRequest {
  external_amounts: ExternalAmountIn[];
  manual_amounts: ManualAmountIn[];
  unit_metrics: UnitMetricIn[];
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
}

export interface EconomicPlanCreate {
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
  id: string;
  organization_id: string;
  property_id: string;
  account_type: BankAccountType;
  label: string;
  iban: string | null;
  bic: string | null;
  bank_name: string | null;
}

export interface BankAccountCreate {
  property_id: string;
  account_type: BankAccountType;
  label: string;
  iban?: string | null;
  bic?: string | null;
  bank_name?: string | null;
}

// --- M6 Payments ---

export type PaymentDirection = "IN" | "OUT";

export type PaymentType =
  | "KALTMIETE"
  | "NK_VORAUSZAHLUNG"
  | "KAUTION"
  | "HAUSGELD_VORSCHUSS"
  | "RUECKLAGENANTEIL"
  | "SONDERZAHLUNG";

export type PaymentStatus = "draft" | "booked";

export interface Payment {
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

export type DunningStatus = "draft" | "issued";

export interface DunningNotice {
  id: string;
  organization_id: string;
  tenant_profile_id: string | null;
  owner_profile_id: string | null;
  level: number;
  issue_date: string;
  due_date: string;
  principal_amount: string;
  fee_amount: string;
  total_amount: string;
  included_items: DunningIncludedItem[] | null;
  status: DunningStatus;
  issued_at: string | null;
  issued_by: string | null;
  note: string | null;
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
  tenant_profile_id: string;
  lease_agreement_id: string;
  period_start: string;
  period_end: string;
  reference_date?: string | null;
  grace_days?: number;
  override_fee?: string | null;
}

export interface DunningCreateOwner {
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
  reserve_opening_balance: string;
  reserve_withdrawals_total: string;
  paid_advances: PaidAdvanceIn[];
}

export interface AssetReportCreate {
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
  reserve_opening_balance: string;
  reserve_withdrawals_total: string;
  reserve_contributions_total: string;
  bank_balances: never[];
  receivables: AssetItemIn[];
  payables: AssetItemIn[];
}

// --- M8 Meetings & Beschluss-Sammlung ---

export type MeetingStatus = "planned" | "held";
export type MeetingType = "ordentlich" | "ausserordentlich";
export type ResolutionResult = "angenommen" | "abgelehnt";

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
  challenge_annotation: string | null;
}

export interface ChallengeAnnotation {
  annotation: string | null;
}
