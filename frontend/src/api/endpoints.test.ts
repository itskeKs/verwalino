import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";

vi.mock("./client", () => ({
  api: { get: vi.fn(), getBlob: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  uploadFormData: vi.fn(),
}));

import { api, uploadFormData } from "./client";
import * as endpoints from "./endpoints";

const RESPONSE = { marker: "response" };
const UPLOADED = { marker: "uploaded" };
const BODY = { marker: "body" } as never;
const KEY_HEADERS = { headers: { "Idempotency-Key": "key-1" } };

type ApiMethod = "get" | "getBlob" | "post" | "put" | "patch" | "delete";
interface CallCase { name: string; run: () => unknown; method: ApiMethod; args: unknown[]; }
interface UrlCase { name: string; run: () => unknown; url: string; }
interface UploadCase { name: string; run: () => unknown; path: string; fields: Record<string, string>; }

const file = () => new File(["synthetic"], "beleg.pdf", { type: "application/pdf" });

const callCases: CallCase[] = [
  { name: "getAiSettings", run: () => endpoints.getAiSettings("org-1"), method: "get", args: ["/api/organizations/org-1/ai-settings"] },
  { name: "saveAiSettings", run: () => endpoints.saveAiSettings("org-1", BODY), method: "put", args: ["/api/organizations/org-1/ai-settings", BODY] },
  { name: "listAiModels without options", run: () => endpoints.listAiModels("org-1"), method: "post", args: ["/api/organizations/org-1/ai-settings/models", {}] },
  { name: "listAiModels with key, provider and base URL", run: () => endpoints.listAiModels("org-1", "sk-test", "openai" as never, "https://llm.example"), method: "post", args: ["/api/organizations/org-1/ai-settings/models", { api_key: "sk-test", provider: "openai", base_url: "https://llm.example" }] },
  { name: "getPreferences", run: () => endpoints.getPreferences(), method: "get", args: ["/api/auth/preferences", { signal: undefined }] },
  { name: "updatePreferences", run: () => endpoints.updatePreferences("klar" as never), method: "patch", args: ["/api/auth/preferences", { design_theme: "klar" }, { signal: undefined }] },
  { name: "getReserveLedger", run: () => endpoints.getReserveLedger("run-1"), method: "get", args: ["/api/weg-settlements/run-1/reserve-ledger"] },
  { name: "addReserveOverride", run: () => endpoints.addReserveOverride("run-1", BODY), method: "post", args: ["/api/weg-settlements/run-1/reserve-ledger/overrides", BODY] },
  { name: "reduceReserveContribution", run: () => endpoints.reduceReserveContribution("run-1", "entry-1", "12.00", "Korrektur"), method: "post", args: ["/api/weg-settlements/run-1/reserve-ledger/overrides/entry-1/reduce", { reduced_to: "12.00", reason: "Korrektur" }] },
  { name: "deleteReserveContribution", run: () => endpoints.deleteReserveContribution("run-1", "entry-1", "Fehlbetrag"), method: "post", args: ["/api/weg-settlements/run-1/reserve-ledger/overrides/entry-1/delete", { reason: "Fehlbetrag" }] },
  { name: "getPropertyYearReport", run: () => endpoints.getPropertyYearReport({ property_id: "p1", year: 2025, accounting_scope_id: "s1" } as never), method: "get", args: ["/api/reports/property-year?property_id=p1&year=2025&accounting_scope_id=s1"] },
  { name: "getPropertyYearReportCsv", run: () => endpoints.getPropertyYearReportCsv({ property_id: "p1", year: 2025 } as never), method: "getBlob", args: ["/api/reports/property-year.csv?property_id=p1&year=2025"] },
  { name: "getAnlageVSummary", run: () => endpoints.getAnlageVSummary({ property_id: "p1", year: 2025 } as never), method: "get", args: ["/api/reports/property-year/anlage-v?property_id=p1&year=2025"] },
  { name: "getAnlageVSummaryCsv", run: () => endpoints.getAnlageVSummaryCsv({ property_id: "p1", year: 2025 } as never), method: "getBlob", args: ["/api/reports/property-year/anlage-v.csv?property_id=p1&year=2025"] },
  { name: "login", run: () => endpoints.login(BODY), method: "post", args: ["/api/auth/login", BODY] },
  { name: "getCurrentSession", run: () => endpoints.getCurrentSession(), method: "get", args: ["/api/auth/session"] },
  { name: "logout", run: () => endpoints.logout(), method: "post", args: ["/api/auth/logout"] },
  { name: "changePassword", run: () => endpoints.changePassword({ old_password: "alt", new_password: "neu-neu" }), method: "post", args: ["/api/auth/password", { old_password: "alt", new_password: "neu-neu" }] },
  { name: "getMyOrganization", run: () => endpoints.getMyOrganization(), method: "get", args: ["/api/organizations/me"] },
  { name: "getOrganizationQuota", run: () => endpoints.getOrganizationQuota(), method: "get", args: ["/api/organizations/quota"] },
  { name: "createMandate", run: () => endpoints.createMandate(BODY), method: "post", args: ["/api/mandates", BODY] },
  { name: "updateMandate encodes the id", run: () => endpoints.updateMandate("m/1", BODY), method: "patch", args: ["/api/mandates/m%2F1", BODY] },
  { name: "getMandate", run: () => endpoints.getMandate("m1"), method: "get", args: ["/api/mandates/m1"] },
  { name: "listMandateDocuments including archived", run: () => endpoints.listMandateDocuments("m1", true), method: "get", args: ["/api/mandates/m1/documents?include_archived=true"] },
  { name: "archiveMandateDocument", run: () => endpoints.archiveMandateDocument("m1", "d1", "Falschablage"), method: "post", args: ["/api/mandates/m1/documents/d1/archive", { reason: "Falschablage" }] },
  { name: "deleteMandateDocument", run: () => endpoints.deleteMandateDocument("m1", "d1"), method: "delete", args: ["/api/mandates/m1/documents/d1"] },
  { name: "listMandates", run: () => endpoints.listMandates(), method: "get", args: ["/api/mandates"] },
  { name: "listProperties", run: () => endpoints.listProperties(), method: "get", args: ["/api/properties"] },
  { name: "getProperty", run: () => endpoints.getProperty("p1"), method: "get", args: ["/api/properties/p1"] },
  { name: "createProperty without idempotency key", run: () => endpoints.createProperty(BODY), method: "post", args: ["/api/properties", BODY, undefined] },
  { name: "createProperty with idempotency key", run: () => endpoints.createProperty(BODY, "key-1"), method: "post", args: ["/api/properties", BODY, KEY_HEADERS] },
  { name: "updateProperty", run: () => endpoints.updateProperty("p1", BODY), method: "put", args: ["/api/properties/p1", BODY] },
  { name: "confirmMeaScale", run: () => endpoints.confirmMeaScale("p1", 1000), method: "post", args: ["/api/properties/p1/mea-scale/confirm", { denominator: 1000, expected_scale_status: "LEGACY_UNCONFIRMED", confirm_existing_values_as_numerators: true }] },
  { name: "createDeclarationImport", run: () => endpoints.createDeclarationImport(BODY), method: "post", args: ["/api/declaration-imports", BODY] },
  { name: "listDeclarationDocuments", run: () => endpoints.listDeclarationDocuments("i1"), method: "get", args: ["/api/declaration-imports/i1/documents"] },
  { name: "updateDeclarationDocument", run: () => endpoints.updateDeclarationDocument("i1", "d1", BODY), method: "patch", args: ["/api/declaration-imports/i1/documents/d1", BODY] },
  { name: "startDeclarationImport", run: () => endpoints.startDeclarationImport("i1", "key-1"), method: "post", args: ["/api/declaration-imports/i1/start", undefined, KEY_HEADERS] },
  { name: "getDeclarationImport", run: () => endpoints.getDeclarationImport("i1"), method: "get", args: ["/api/declaration-imports/i1"] },
  { name: "listDeclarationCandidates", run: () => endpoints.listDeclarationCandidates("i1"), method: "get", args: ["/api/declaration-imports/i1/candidates"] },
  { name: "reviewDeclarationCandidate", run: () => endpoints.reviewDeclarationCandidate("i1", "c1", BODY, "key-1"), method: "patch", args: ["/api/declaration-imports/i1/candidates/c1", BODY, KEY_HEADERS] },
  { name: "bulkReviewDeclarationCandidates", run: () => endpoints.bulkReviewDeclarationCandidates("i1", BODY, "key-1"), method: "post", args: ["/api/declaration-imports/i1/candidates/bulk-review", BODY, KEY_HEADERS] },
  { name: "getDeclarationApprovalPreview", run: () => endpoints.getDeclarationApprovalPreview("i1"), method: "get", args: ["/api/declaration-imports/i1/approval-preview"] },
  { name: "approveDeclarationImport", run: () => endpoints.approveDeclarationImport("i1", BODY), method: "post", args: ["/api/declaration-imports/i1/approve", BODY] },
  { name: "retryDeclarationImport", run: () => endpoints.retryDeclarationImport("i1", "key-1"), method: "post", args: ["/api/declaration-imports/i1/retry", undefined, KEY_HEADERS] },
  { name: "cancelDeclarationImport", run: () => endpoints.cancelDeclarationImport("i1"), method: "post", args: ["/api/declaration-imports/i1/cancel"] },
  { name: "listPropertyDocuments", run: () => endpoints.listPropertyDocuments("p1"), method: "get", args: ["/api/properties/p1/documents"] },
  { name: "getPropertyKnowledge with history", run: () => endpoints.getPropertyKnowledge("p1", { includeHistory: true }), method: "get", args: ["/api/properties/p1/knowledge?include_history=true", { signal: undefined }] },
  { name: "listPropertyRules without history", run: () => endpoints.listPropertyRules("p1"), method: "get", args: ["/api/properties/p1/rules", { signal: undefined }] },
  { name: "searchPropertyKnowledge", run: () => endpoints.searchPropertyKnowledge("p1", "Heizung?", { includeHistory: true, limit: 5 }), method: "get", args: ["/api/properties/p1/knowledge/search?q=Heizung%3F&include_history=true&limit=5", { signal: undefined }] },
  { name: "queryPropertyKnowledge", run: () => endpoints.queryPropertyKnowledge("p1", "Welche Heizung?"), method: "post", args: ["/api/properties/p1/knowledge/query", { question: "Welche Heizung?" }] },
  { name: "deleteProperty encodes the id", run: () => endpoints.deleteProperty("p/1"), method: "delete", args: ["/api/properties/p%2F1", { signal: undefined }] },
  { name: "listUnits without property", run: () => endpoints.listUnits(), method: "get", args: ["/api/units"] },
  { name: "listUnits filtered by property", run: () => endpoints.listUnits("p1"), method: "get", args: ["/api/units?property_id=p1"] },
  { name: "getUnit", run: () => endpoints.getUnit("u1"), method: "get", args: ["/api/units/u1"] },
  { name: "createUnit", run: () => endpoints.createUnit(BODY), method: "post", args: ["/api/units", BODY] },
  { name: "updateUnit", run: () => endpoints.updateUnit("u1", BODY), method: "put", args: ["/api/units/u1", BODY] },
  { name: "deleteUnit", run: () => endpoints.deleteUnit("u1"), method: "delete", args: ["/api/units/u1", { signal: undefined }] },
  { name: "listPersons", run: () => endpoints.listPersons(), method: "get", args: ["/api/persons"] },
  { name: "getPerson", run: () => endpoints.getPerson("per-1"), method: "get", args: ["/api/persons/per-1"] },
  { name: "createPerson", run: () => endpoints.createPerson(BODY), method: "post", args: ["/api/persons", BODY] },
  { name: "updatePerson", run: () => endpoints.updatePerson("per-1", BODY), method: "put", args: ["/api/persons/per-1", BODY] },
  { name: "deletePerson encodes the id", run: () => endpoints.deletePerson("per 1"), method: "delete", args: ["/api/persons/per%201", { signal: undefined }] },
  { name: "listOwnerProfiles", run: () => endpoints.listOwnerProfiles("per-1"), method: "get", args: ["/api/owner-profiles?person_id=per-1"] },
  { name: "createOwnerProfile with notes", run: () => endpoints.createOwnerProfile("per-1", "Notiz"), method: "post", args: ["/api/owner-profiles", { person_id: "per-1", notes: "Notiz" }] },
  { name: "listTenantProfiles", run: () => endpoints.listTenantProfiles("per-1"), method: "get", args: ["/api/tenant-profiles?person_id=per-1"] },
  { name: "createTenantProfile without notes", run: () => endpoints.createTenantProfile("per-1"), method: "post", args: ["/api/tenant-profiles", { person_id: "per-1", notes: "" }] },
  { name: "listVendors", run: () => endpoints.listVendors("per-1"), method: "get", args: ["/api/vendors?person_id=per-1"] },
  { name: "createVendor", run: () => endpoints.createVendor({ person_id: "per-1", tax_id: "12/345/67890" }), method: "post", args: ["/api/vendors", { person_id: "per-1", tax_id: "12/345/67890" }] },
  { name: "updateVendor encodes the id", run: () => endpoints.updateVendor("v/1", BODY), method: "put", args: ["/api/vendors/v%2F1", BODY] },
  { name: "listOwnership", run: () => endpoints.listOwnership("u1"), method: "get", args: ["/api/ownership?unit_id=u1"] },
  { name: "createOwnership", run: () => endpoints.createOwnership(BODY), method: "post", args: ["/api/ownership", BODY] },
  { name: "patchOwnership", run: () => endpoints.patchOwnership("o1", BODY), method: "patch", args: ["/api/ownership/o1", BODY] },
  { name: "listLeases", run: () => endpoints.listLeases("u1"), method: "get", args: ["/api/leases?unit_id=u1"] },
  { name: "createLease", run: () => endpoints.createLease(BODY), method: "post", args: ["/api/leases", BODY] },
  { name: "patchLease", run: () => endpoints.patchLease("l1", BODY), method: "patch", args: ["/api/leases/l1", BODY] },
  { name: "deleteLease encodes the id", run: () => endpoints.deleteLease("l 1"), method: "delete", args: ["/api/leases/l%201", { signal: undefined }] },
  { name: "listLeaseRentChanges", run: () => endpoints.listLeaseRentChanges("l1"), method: "get", args: ["/api/leases/l1/rent-changes"] },
  { name: "createLeaseRentChange", run: () => endpoints.createLeaseRentChange("l1", BODY), method: "post", args: ["/api/leases/l1/rent-changes", BODY] },
  { name: "createStaffelmieteRentChanges", run: () => endpoints.createStaffelmieteRentChanges("l1", BODY), method: "post", args: ["/api/leases/l1/rent-changes/staffel", BODY] },
  { name: "previewIndexmieteRentChange", run: () => endpoints.previewIndexmieteRentChange("l1", BODY), method: "post", args: ["/api/leases/l1/rent-changes/index/preview", BODY] },
  { name: "createIndexmieteRentChange", run: () => endpoints.createIndexmieteRentChange("l1", BODY), method: "post", args: ["/api/leases/l1/rent-changes/index", BODY] },
  { name: "listCostCategories", run: () => endpoints.listCostCategories(), method: "get", args: ["/api/cost-categories"] },
  { name: "listCostEntries with filters in stable order", run: () => endpoints.listCostEntries({ status: "NEU", property_id: "p1" }), method: "get", args: ["/api/cost-entries?status=NEU&property_id=p1"] },
  { name: "exportCostEntriesCsv", run: () => endpoints.exportCostEntriesCsv({ status: "NEU" }), method: "getBlob", args: ["/api/cost-entries/export.csv?status=NEU"] },
  { name: "getCostEntry", run: () => endpoints.getCostEntry("c1"), method: "get", args: ["/api/cost-entries/c1"] },
  { name: "createCostEntry with idempotency key", run: () => endpoints.createCostEntry(BODY, "key-1"), method: "post", args: ["/api/cost-entries", BODY, KEY_HEADERS] },
  { name: "updateCostEntry", run: () => endpoints.updateCostEntry("c1", BODY), method: "patch", args: ["/api/cost-entries/c1", BODY] },
  { name: "confirmCostEntry", run: () => endpoints.confirmCostEntry("c1"), method: "post", args: ["/api/cost-entries/c1/confirm"] },
  { name: "rejectCostEntry", run: () => endpoints.rejectCostEntry("c1"), method: "post", args: ["/api/cost-entries/c1/reject"] },
  { name: "createCostEntryCorrection", run: () => endpoints.createCostEntryCorrection("c1", BODY), method: "post", args: ["/api/cost-entries/c1/correction", BODY] },
  { name: "retryInvoiceExtraction", run: () => endpoints.retryInvoiceExtraction("d1"), method: "post", args: ["/api/invoice-documents/d1/retry"] },
  { name: "listInvoiceDocuments encodes the filter", run: () => endpoints.listInvoiceDocuments("c 1"), method: "get", args: ["/api/invoice-documents?cost_entry_id=c%201"] },
  { name: "getInvoiceDocument", run: () => endpoints.getInvoiceDocument("d1"), method: "get", args: ["/api/invoice-documents/d1"] },
  { name: "verifyInvoiceDocument", run: () => endpoints.verifyInvoiceDocument("d1"), method: "get", args: ["/api/invoice-documents/d1/verify"] },
];

callCases.push(
  { name: "listSettlements filtered by property", run: () => endpoints.listSettlements("p1"), method: "get", args: ["/api/settlements?property_id=p1"] },
  { name: "getSettlement", run: () => endpoints.getSettlement("s1"), method: "get", args: ["/api/settlements/s1"] },
  { name: "getAdvanceProposal with accept_from", run: () => endpoints.getAdvanceProposal("s1", "2026-01-01"), method: "get", args: ["/api/settlements/s1/advance-proposal?accept_from=2026-01-01"] },
  { name: "applyAdvanceProposal keeps only valid_from", run: () => endpoints.applyAdvanceProposal("s1", { valid_from: "2026-02-01", server_field: "x" } as never), method: "post", args: ["/api/settlements/s1/advance-proposal", { valid_from: "2026-02-01" }] },
  { name: "createSettlement", run: () => endpoints.createSettlement(BODY), method: "post", args: ["/api/settlements", BODY] },
  { name: "createSettlementBatch", run: () => endpoints.createSettlementBatch(BODY), method: "post", args: ["/api/settlements/batch", BODY] },
  { name: "setAdvancePayments", run: () => endpoints.setAdvancePayments("s1", BODY), method: "patch", args: ["/api/settlements/s1/advance-payments", BODY] },
  { name: "getHeatingConsumption encodes the run id", run: () => endpoints.getHeatingConsumption("r 1", "c1", "WASSER" as never, "FLAECHE" as never), method: "get", args: ["/api/settlements/r%201/heating-consumption?cost_entry_id=c1&meter_type=WASSER&base_key=FLAECHE"] },
  { name: "computeSettlement without body posts null", run: () => endpoints.computeSettlement("s1"), method: "post", args: ["/api/settlements/s1/compute", null] },
  { name: "previewSettlement with body", run: () => endpoints.previewSettlement("s1", BODY), method: "post", args: ["/api/settlements/s1/preview", BODY] },
  { name: "finalizeSettlement without body posts null", run: () => endpoints.finalizeSettlement("s1"), method: "post", args: ["/api/settlements/s1/finalize", null] },
  { name: "createSettlementCorrection", run: () => endpoints.createSettlementCorrection("s1"), method: "post", args: ["/api/settlements/s1/correction"] },
  { name: "listEconomicPlans filtered by property", run: () => endpoints.listEconomicPlans("p1"), method: "get", args: ["/api/economic-plans?property_id=p1"] },
  { name: "getEconomicPlan", run: () => endpoints.getEconomicPlan("e1"), method: "get", args: ["/api/economic-plans/e1"] },
  { name: "createEconomicPlan", run: () => endpoints.createEconomicPlan(BODY), method: "post", args: ["/api/economic-plans", BODY] },
  { name: "computeEconomicPlan", run: () => endpoints.computeEconomicPlan("e1", BODY), method: "post", args: ["/api/economic-plans/e1/compute", BODY] },
  { name: "finalizeEconomicPlan", run: () => endpoints.finalizeEconomicPlan("e1", BODY), method: "post", args: ["/api/economic-plans/e1/finalize", BODY] },
  { name: "createEconomicPlanCorrection", run: () => endpoints.createEconomicPlanCorrection("e1"), method: "post", args: ["/api/economic-plans/e1/correction"] },
  { name: "listWegRuns encodes filters in stable order", run: () => endpoints.listWegRuns("WEG_JAHRESABRECHNUNG", "p 1"), method: "get", args: ["/api/weg-settlements?run_type=WEG_JAHRESABRECHNUNG&property_id=p%201"] },
  { name: "getWegRun", run: () => endpoints.getWegRun("w1"), method: "get", args: ["/api/weg-settlements/w1"] },
  { name: "deleteWegDraft", run: () => endpoints.deleteWegDraft("w1"), method: "delete", args: ["/api/weg-settlements/w1"] },
  { name: "deleteWegDraft of an asset report", run: () => endpoints.deleteWegDraft("w1", true), method: "delete", args: ["/api/weg-settlements/asset-reports/w1"] },
  { name: "getWegInputOwners", run: () => endpoints.getWegInputOwners("w1"), method: "get", args: ["/api/weg-settlements/w1/input-owners"] },
  { name: "createWegRun", run: () => endpoints.createWegRun(BODY), method: "post", args: ["/api/weg-settlements", BODY] },
  { name: "computeWegRun", run: () => endpoints.computeWegRun("w1", BODY), method: "post", args: ["/api/weg-settlements/w1/compute", BODY] },
  { name: "finalizeWegRun", run: () => endpoints.finalizeWegRun("w1", BODY), method: "post", args: ["/api/weg-settlements/w1/finalize", BODY] },
  { name: "createAssetReport", run: () => endpoints.createAssetReport(BODY), method: "post", args: ["/api/weg-settlements/asset-reports", BODY] },
  { name: "computeAssetReport", run: () => endpoints.computeAssetReport("a1", BODY), method: "post", args: ["/api/weg-settlements/asset-reports/a1/compute", BODY] },
  { name: "finalizeAssetReport", run: () => endpoints.finalizeAssetReport("a1", BODY), method: "post", args: ["/api/weg-settlements/asset-reports/a1/finalize", BODY] },
  { name: "listBankAccounts filtered by property", run: () => endpoints.listBankAccounts("p1"), method: "get", args: ["/api/bank-accounts?property_id=p1"] },
  { name: "getBankAccount", run: () => endpoints.getBankAccount("b1"), method: "get", args: ["/api/bank-accounts/b1"] },
  { name: "createBankAccount", run: () => endpoints.createBankAccount(BODY), method: "post", args: ["/api/bank-accounts", BODY] },
  { name: "updateBankAccount encodes the id", run: () => endpoints.updateBankAccount("b/1", BODY), method: "patch", args: ["/api/bank-accounts/b%2F1", BODY] },
  { name: "listBankTransactions with status and offset", run: () => endpoints.listBankTransactions("b1", "NEU" as never, 10), method: "get", args: ["/api/bank-accounts/b1/transactions?offset=10&limit=50&status=NEU"] },
  { name: "listBankMatchingTransactions", run: () => endpoints.listBankMatchingTransactions("b1", "ALL", 5), method: "get", args: ["/api/bank-transactions?bank_account_id=b1&status=ALL&offset=5&limit=50"] },
  { name: "assignBankTransaction to a cost entry", run: () => endpoints.assignBankTransaction("t1", "cost-1"), method: "post", args: ["/api/bank-transactions/t1/assign", { cost_entry_id: "cost-1" }] },
  { name: "assignBankTransaction with split lines", run: () => endpoints.assignBankTransaction("t1", [{ cost_entry_id: "c1", amount: "10.00" }] as never), method: "post", args: ["/api/bank-transactions/t1/assign", { lines: [{ cost_entry_id: "c1", amount: "10.00" }] }] },
  { name: "ignoreBankTransaction", run: () => endpoints.ignoreBankTransaction("t1"), method: "post", args: ["/api/bank-transactions/t1/ignore"] },
  { name: "listDeposits encodes the scope", run: () => endpoints.listDeposits("s 1"), method: "get", args: ["/api/payments/deposits?accounting_scope_id=s%201"] },
  { name: "listPayments keeps the filter order and skips empty values", run: () => endpoints.listPayments({ owner_profile_id: "o1", payment_type: "", status: "GEBUCHT" }), method: "get", args: ["/api/payments?owner_profile_id=o1&status=GEBUCHT"] },
  { name: "exportPaymentsCsv", run: () => endpoints.exportPaymentsCsv({ status: "GEBUCHT" }), method: "getBlob", args: ["/api/payments/export.csv?status=GEBUCHT"] },
  { name: "getPayment", run: () => endpoints.getPayment("p1"), method: "get", args: ["/api/payments/p1"] },
  { name: "createPayment", run: () => endpoints.createPayment(BODY), method: "post", args: ["/api/payments", BODY] },
  { name: "updatePayment", run: () => endpoints.updatePayment("p1", BODY), method: "patch", args: ["/api/payments/p1", BODY] },
  { name: "getHausgeldBooking", run: () => endpoints.getHausgeldBooking("plan-1"), method: "get", args: ["/api/economic-plans/plan-1/hausgeld-booking"] },
  { name: "bookHausgeld", run: () => endpoints.bookHausgeld("plan-1", { bank_account_id: "b1", rows: [] }), method: "post", args: ["/api/economic-plans/plan-1/hausgeld-booking", { bank_account_id: "b1", rows: [] }] },
  { name: "bookPayment", run: () => endpoints.bookPayment("p1"), method: "post", args: ["/api/payments/p1/book"] },
  { name: "reversePayment with note", run: () => endpoints.reversePayment("p1", "Storno"), method: "post", args: ["/api/payments/p1/reverse", { note: "Storno" }] },
  { name: "reversePayment without note posts an empty object", run: () => endpoints.reversePayment("p1"), method: "post", args: ["/api/payments/p1/reverse", {}] },
  { name: "deletePayment", run: () => endpoints.deletePayment("p1"), method: "delete", args: ["/api/payments/p1"] },
  { name: "leaseStatement with scope and as_of", run: () => endpoints.leaseStatement("l1", "2026-01-01", "2026-12-31", "s1", "2026-06-01"), method: "get", args: ["/api/payments/mietkonto/lease/l1?period_start=2026-01-01&period_end=2026-12-31&accounting_scope_id=s1&as_of=2026-06-01"] },
  { name: "ownerStatement encodes the plan id", run: () => endpoints.ownerStatement("o1", "2026-01-01", "2026-12-31", "plan/1", "s1"), method: "get", args: ["/api/payments/mietkonto/owner/o1?period_start=2026-01-01&period_end=2026-12-31&accounting_scope_id=s1&economic_plan_id=plan%2F1"] },
  { name: "leaseOpenItems", run: () => endpoints.leaseOpenItems("l1", "2026-01-01", "2026-12-31", "s1"), method: "get", args: ["/api/payments/open-items/lease/l1?period_start=2026-01-01&period_end=2026-12-31&accounting_scope_id=s1"] },
  { name: "allLeaseOpenItems", run: () => endpoints.allLeaseOpenItems("2026-01-01", "2026-12-31", "s1"), method: "get", args: ["/api/payments/open-items?period_start=2026-01-01&period_end=2026-12-31&accounting_scope_id=s1"] },
  { name: "setAdvancePaymentsDerived", run: () => endpoints.setAdvancePaymentsDerived("run-1", 2026), method: "patch", args: ["/api/settlements/run-1/advance-payments-derived?period_year=2026"] },
  { name: "getDunningSettings", run: () => endpoints.getDunningSettings(), method: "get", args: ["/api/dunning/settings"] },
  { name: "saveDunningSettings", run: () => endpoints.saveDunningSettings(BODY), method: "put", args: ["/api/dunning/settings", BODY] },
  { name: "listDunningNotices encodes filters in stable order", run: () => endpoints.listDunningNotices({ tenant_profile_id: "t 1", level: 2, status: "ENTWURF" }), method: "get", args: ["/api/dunning?tenant_profile_id=t%201&level=2&status=ENTWURF"] },
  { name: "getDunningNotice", run: () => endpoints.getDunningNotice("d1"), method: "get", args: ["/api/dunning/d1"] },
  { name: "createDunningForTenant", run: () => endpoints.createDunningForTenant(BODY), method: "post", args: ["/api/dunning/tenant", BODY] },
  { name: "createDunningForOwner", run: () => endpoints.createDunningForOwner(BODY), method: "post", args: ["/api/dunning/owner", BODY] },
  { name: "issueDunningNotice", run: () => endpoints.issueDunningNotice("d1"), method: "post", args: ["/api/dunning/d1/issue"] },
  { name: "deleteDunningNotice", run: () => endpoints.deleteDunningNotice("d1"), method: "delete", args: ["/api/dunning/d1"] },
  { name: "listMeetings filtered by property", run: () => endpoints.listMeetings("p1"), method: "get", args: ["/api/meetings?property_id=p1"] },
  { name: "getMeeting", run: () => endpoints.getMeeting("m1"), method: "get", args: ["/api/meetings/m1"] },
  { name: "createMeeting", run: () => endpoints.createMeeting(BODY), method: "post", args: ["/api/meetings", BODY] },
  { name: "updateMeeting", run: () => endpoints.updateMeeting("m1", BODY), method: "put", args: ["/api/meetings/m1", BODY] },
  { name: "markMeetingHeld", run: () => endpoints.markMeetingHeld("m1"), method: "post", args: ["/api/meetings/m1/hold"] },
  { name: "deleteMeeting", run: () => endpoints.deleteMeeting("m1"), method: "delete", args: ["/api/meetings/m1"] },
  { name: "listAgendaItems", run: () => endpoints.listAgendaItems("m1"), method: "get", args: ["/api/meetings/m1/agenda"] },
  { name: "createAgendaItem", run: () => endpoints.createAgendaItem("m1", BODY), method: "post", args: ["/api/meetings/m1/agenda", BODY] },
  { name: "updateAgendaItem", run: () => endpoints.updateAgendaItem("item-1", BODY), method: "put", args: ["/api/meetings/agenda/item-1", BODY] },
  { name: "deleteAgendaItem", run: () => endpoints.deleteAgendaItem("item-1"), method: "delete", args: ["/api/meetings/agenda/item-1"] },
  { name: "listResolutions", run: () => endpoints.listResolutions("m1"), method: "get", args: ["/api/meetings/m1/resolutions"] },
  { name: "createResolution", run: () => endpoints.createResolution(BODY), method: "post", args: ["/api/meetings/resolutions", BODY] },
  { name: "deleteResolution", run: () => endpoints.deleteResolution("r1"), method: "delete", args: ["/api/meetings/resolutions/r1"] },
  { name: "enterIntoRegister", run: () => endpoints.enterIntoRegister("r1"), method: "post", args: ["/api/meetings/resolutions/r1/register"] },
  { name: "listRegisterEntries", run: () => endpoints.listRegisterEntries("p1"), method: "get", args: ["/api/meetings/register/p1"] },
  { name: "annotateChallenge", run: () => endpoints.annotateChallenge("e1", BODY), method: "put", args: ["/api/meetings/register/entry/e1/challenge", BODY] },
  { name: "listFristen without filters keeps the trailing question mark", run: () => endpoints.listFristen(), method: "get", args: ["/api/fristen?"] },
  { name: "listFristen with both filters", run: () => endpoints.listFristen("2026-01-01", "s1"), method: "get", args: ["/api/fristen?reference_date=2026-01-01&accounting_scope_id=s1"] },
  { name: "getDsgvoStatus", run: () => endpoints.getDsgvoStatus("per-1"), method: "get", args: ["/api/dsgvo/persons/per-1/status"] },
  { name: "getDsgvoExport", run: () => endpoints.getDsgvoExport("per-1"), method: "get", args: ["/api/dsgvo/persons/per-1/export"] },
  { name: "requestPersonDeletion", run: () => endpoints.requestPersonDeletion("per-1"), method: "post", args: ["/api/dsgvo/persons/per-1/request-deletion"] },
  { name: "anonymizePerson", run: () => endpoints.anonymizePerson("per-1"), method: "post", args: ["/api/dsgvo/persons/per-1/anonymize"] },
  { name: "sourceAdmissionReviewApi.get", run: () => endpoints.sourceAdmissionReviewApi.get("r1"), method: "get", args: ["/api/property-source-admission-reviews/r1", { signal: undefined }] },
  { name: "sourceAdmissionReviewApi.review with idempotency key", run: () => endpoints.sourceAdmissionReviewApi.review(BODY, "key-1"), method: "post", args: ["/api/property-source-admission-reviews", BODY, KEY_HEADERS] },
  { name: "sourceClassificationApi.get", run: () => endpoints.sourceClassificationApi.get("c1"), method: "get", args: ["/api/property-source-classifications/c1", { signal: undefined }] },
  { name: "sourceClassificationApi.classify with idempotency key", run: () => endpoints.sourceClassificationApi.classify(BODY, "key-1"), method: "post", args: ["/api/property-source-classifications", BODY, KEY_HEADERS] },
  { name: "createUser maps the fields", run: () => endpoints.createUser("admin@example.org", "geheim-123", "Admin", "CLERK"), method: "post", args: ["/api/users", { email: "admin@example.org", password: "geheim-123", display_name: "Admin", role: "CLERK" }] },
  { name: "updateUser encodes the id", run: () => endpoints.updateUser("u/1", { role: "CLERK" as never }), method: "patch", args: ["/api/users/u%2F1", { role: "CLERK" }] },
  { name: "resetUserPassword", run: () => endpoints.resetUserPassword("u1", "geheim-123"), method: "post", args: ["/api/users/u1/reset-password", { password: "geheim-123" }] },
  { name: "createUserInvitation", run: () => endpoints.createUserInvitation("kollegin@example.org", "CLERK"), method: "post", args: ["/api/users/invitations", { email: "kollegin@example.org", role: "CLERK" }] },
  { name: "listUserInvitations", run: () => endpoints.listUserInvitations(), method: "get", args: ["/api/users/invitations"] },
  { name: "revokeUserInvitation encodes the id", run: () => endpoints.revokeUserInvitation("i/1"), method: "delete", args: ["/api/users/invitations/i%2F1"] },
  { name: "resendUserInvitation encodes the id", run: () => endpoints.resendUserInvitation("i 1"), method: "post", args: ["/api/users/invitations/i%201/resend"] },
  { name: "derivePaidAdvances", run: () => endpoints.derivePaidAdvances("run-1"), method: "post", args: ["/api/weg-settlements/run-1/paid-advances-derived"] },
  { name: "getConsumptionSuggestions encodes the run id", run: () => endpoints.getConsumptionSuggestions("run 1"), method: "get", args: ["/api/settlements/run%201/consumption-suggestions"] },
  { name: "listMeters with unit filter", run: () => endpoints.listMeters("p1", "u1"), method: "get", args: ["/api/meters?property_id=p1&unit_id=u1"] },
  { name: "createMeter fills unit and location defaults", run: () => endpoints.createMeter({ property_id: "p1", meter_number: "M-1", meter_type: "STROM" } as never), method: "post", args: ["/api/meters", { property_id: "p1", unit_id: null, meter_number: "M-1", meter_type: "STROM", location: "" }] },
  { name: "updateMeter keeps only meter fields", run: () => endpoints.updateMeter("m1", { meter_number: "M-2", meter_type: "GAS", server_field: "x" } as never), method: "patch", args: ["/api/meters/m1", { meter_number: "M-2", meter_type: "GAS" }] },
  { name: "listMeterReadings", run: () => endpoints.listMeterReadings("m1"), method: "get", args: ["/api/meters/m1/readings"] },
  { name: "createMeterReading fills the period default", run: () => endpoints.createMeterReading("m1", { reading_date: "2026-01-15", value: "123.4" } as never), method: "post", args: ["/api/meters/m1/readings", { reading_date: "2026-01-15", value: "123.4", period: null }] },
  { name: "listSpecialLevies encodes both filters", run: () => endpoints.listSpecialLevies("s 1", "p 1"), method: "get", args: ["/api/special-levies?accounting_scope_id=s%201&property_id=p%201"] },
  { name: "createSpecialLevy keeps only the input fields", run: () => endpoints.createSpecialLevy({ property_id: "p1", accounting_scope_id: "s1", amount: "100.00", due_date: "2026-02-01", frozen: true } as never), method: "post", args: ["/api/special-levies", { property_id: "p1", accounting_scope_id: "s1", amount: "100.00", due_date: "2026-02-01" }] },
  { name: "linkSpecialLevyResolution", run: () => endpoints.linkSpecialLevyResolution("sl1", "r1"), method: "put", args: ["/api/special-levies/sl1/resolution", { resolution_id: "r1" }] },
  { name: "listAttachments keeps the filter order", run: () => endpoints.listAttachments({ property_id: "p1", include_archived: true, offset: 5, limit: 10 } as never), method: "get", args: ["/api/attachments?property_id=p1&include_archived=true&offset=5&limit=10"] },
  { name: "getAttachment encodes the id", run: () => endpoints.getAttachment("a/1"), method: "get", args: ["/api/attachments/a%2F1"] },
  { name: "archiveAttachment", run: () => endpoints.archiveAttachment("a1", "Falschablage"), method: "post", args: ["/api/attachments/a1/archive", { reason: "Falschablage" }] },
  { name: "listTasks without filters", run: () => endpoints.listTasks(), method: "get", args: ["/api/tasks"] },
  { name: "listTasks keeps the filter order", run: () => endpoints.listTasks({ property_id: "p1", completed: false, limit: 20 } as never), method: "get", args: ["/api/tasks?property_id=p1&completed=false&limit=20"] },
  { name: "getTask encodes the id", run: () => endpoints.getTask("t/1"), method: "get", args: ["/api/tasks/t%2F1"] },
  { name: "createTask keeps only the allowlisted fields", run: () => endpoints.createTask({ title: "Dach prüfen", completed: false, server_field: "x" } as never), method: "post", args: ["/api/tasks", { title: "Dach prüfen", completed: false }] },
  { name: "updateTask keeps only the allowlisted fields", run: () => endpoints.updateTask("t1", { note: "Erledigt", server_field: "x" } as never), method: "patch", args: ["/api/tasks/t1", { note: "Erledigt" }] },
  { name: "deleteTask", run: () => endpoints.deleteTask("t1"), method: "delete", args: ["/api/tasks/t1"] },
  { name: "getFristenContext", run: () => endpoints.getFristenContext(), method: "get", args: ["/api/fristen/context"] },
  { name: "listSettlementPeriods with year", run: () => endpoints.listSettlementPeriods("p1", 2026), method: "get", args: ["/api/properties/p1/settlement-periods?year=2026"] },
  { name: "getUnitHousehold encodes the id", run: () => endpoints.getUnitHousehold("u 1"), method: "get", args: ["/api/units/u%201/household"] },
  { name: "replaceUnitHousehold", run: () => endpoints.replaceUnitHousehold("u1", [{ name: "Anna" }] as never), method: "put", args: ["/api/units/u1/household", [{ name: "Anna" }]] },
);

const urlCases: UrlCase[] = [
  { name: "anlageVCsvUrl", run: () => endpoints.anlageVCsvUrl({ property_id: "p1", year: 2025, accounting_scope_id: "s1" } as never), url: "/api/reports/property-year/anlage-v.csv?property_id=p1&year=2025&accounting_scope_id=s1" },
  { name: "mandateDocumentDownloadUrl", run: () => endpoints.mandateDocumentDownloadUrl("m1", "d1"), url: "/api/mandates/m1/documents/d1/download" },
  { name: "propertyDocumentContentUrl without page", run: () => endpoints.propertyDocumentContentUrl("p1", "d1"), url: "/api/properties/p1/documents/d1/content" },
  { name: "propertyDocumentContentUrl with page anchor", run: () => endpoints.propertyDocumentContentUrl("p1", "d1", 3), url: "/api/properties/p1/documents/d1/content#page=3" },
  { name: "declarationImportDocumentContentUrl", run: () => endpoints.declarationImportDocumentContentUrl("i1", "d1"), url: "/api/declaration-imports/i1/documents/d1/content" },
  { name: "invoiceDocumentContentUrl", run: () => endpoints.invoiceDocumentContentUrl("d1"), url: "/api/invoice-documents/d1/content" },
  { name: "settlementPdfUrl", run: () => endpoints.settlementPdfUrl("s1"), url: "/api/settlements/s1/pdf" },
  { name: "economicPlanPdfUrl", run: () => endpoints.economicPlanPdfUrl("e1"), url: "/api/economic-plans/e1/pdf" },
  { name: "wegRunPdfUrl", run: () => endpoints.wegRunPdfUrl("w1"), url: "/api/weg-settlements/w1/pdf" },
  { name: "assetReportPdfUrl", run: () => endpoints.assetReportPdfUrl("a1"), url: "/api/weg-settlements/asset-reports/a1/pdf" },
  { name: "dunningPdfUrl", run: () => endpoints.dunningPdfUrl("d1"), url: "/api/dunning/d1/pdf" },
  { name: "meetingInvitationPdfUrl", run: () => endpoints.meetingInvitationPdfUrl("m1"), url: "/api/meetings/m1/invitation.pdf" },
  { name: "meetingProtocolPdfUrl", run: () => endpoints.meetingProtocolPdfUrl("m1"), url: "/api/meetings/m1/pdf" },
  { name: "registerPdfUrl", run: () => endpoints.registerPdfUrl("p1"), url: "/api/meetings/register/p1/pdf" },
  { name: "attachmentDownloadUrl defaults to a download", run: () => endpoints.attachmentDownloadUrl("a1"), url: "/api/attachments/a1/download" },
  { name: "attachmentDownloadUrl inline", run: () => endpoints.attachmentDownloadUrl("a1", true), url: "/api/attachments/a1/download?inline=true" },
];

const uploadCases: UploadCase[] = [
  { name: "uploadMandateDocument", run: () => endpoints.uploadMandateDocument("m1", (() => { const fd = new FormData(); fd.append("file", file()); return fd; })()), path: "/api/mandates/m1/documents", fields: {} },
  { name: "uploadDeclarationDocument", run: () => endpoints.uploadDeclarationDocument("i1", (() => { const fd = new FormData(); fd.append("file", file()); return fd; })()), path: "/api/declaration-imports/i1/documents", fields: {} },
  { name: "uploadInvoiceDocument with cost entry link", run: () => endpoints.uploadInvoiceDocument(file(), "c1"), path: "/api/invoice-documents/upload", fields: { cost_entry_id: "c1" } },
  { name: "createInvoiceDocumentVersion without cost entry link", run: () => endpoints.createInvoiceDocumentVersion("d1", file()), path: "/api/invoice-documents/d1/version", fields: {} },
  { name: "importBankTransactions as final run", run: () => endpoints.importBankTransactions("b1", file(), false), path: "/api/bank-accounts/b1/imports?dry_run=false", fields: {} },
  { name: "uploadAttachment keeps optional links", run: () => endpoints.uploadAttachment(file(), { property_id: "p1", title: "Kaufvertrag" } as never), path: "/api/attachments", fields: { property_id: "p1", title: "Kaufvertrag" } },
];

beforeEach(() => {
  vi.clearAllMocks();
  for (const method of ["get", "getBlob", "post", "put", "patch", "delete"] as const) {
    (api[method] as unknown as Mock).mockResolvedValue(RESPONSE);
  }
  (uploadFormData as unknown as Mock).mockResolvedValue(UPLOADED);
});

describe("HTTP calls", () => {
  it.each(callCases)("$name", async ({ run, method, args }) => {
    const result = await run();
    expect(result).toBe(RESPONSE);
    const spy = api[method] as unknown as Mock;
    expect(spy).toHaveBeenCalledTimes(1);
    expect([...spy.mock.calls[0]]).toEqual(args);
  });
});

describe("URL builders", () => {
  it.each(urlCases)("$name", ({ run, url }) => {
    expect(run()).toBe(url);
  });
});

describe("uploads", () => {
  it.each(uploadCases)("$name", async ({ run, path, fields }) => {
    const result = await run();
    expect(result).toBe(UPLOADED);
    const spy = uploadFormData as unknown as Mock;
    expect(spy).toHaveBeenCalledTimes(1);
    const [calledPath, data] = spy.mock.calls[0] as [string, FormData];
    expect(calledPath).toBe(path);
    expect(data.get("file")).toBeInstanceOf(File);
    for (const [key, value] of Object.entries(fields)) expect(data.get(key)).toBe(value);
  });
});

describe("listTaskAssignees", () => {
  it("follows the cursor until the last page and concatenates the users", async () => {
    const anna = { id: "user-anna" };
    const bert = { id: "user-bert" };
    (api.get as unknown as Mock)
      .mockResolvedValueOnce({ items: [anna], next_cursor: "cursor-2" })
      .mockResolvedValueOnce({ items: [bert], next_cursor: null });

    const users = await endpoints.listTaskAssignees();

    expect(users).toEqual([anna, bert]);
    expect((api.get as unknown as Mock).mock.calls).toEqual([
      ["/api/users/page?eligible_for_scope=true"],
      ["/api/users/page?eligible_for_scope=true&cursor=cursor-2"],
    ]);
  });
});

describe("pollDeclarationImport", () => {
  it("delivers a terminal status once and stops polling", async () => {
    (api.get as unknown as Mock).mockResolvedValue({ status: "APPLIED" });
    const onUpdate = vi.fn();
    const onError = vi.fn();
    const stop = endpoints.pollDeclarationImport("i1", { onUpdate, onError, intervalMs: 5 });

    await vi.waitFor(() => expect(onUpdate).toHaveBeenCalledWith({ status: "APPLIED" }));
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(onError).not.toHaveBeenCalled();
    stop();
  });

  it("keeps polling a running import until the stop function is called", async () => {
    (api.get as unknown as Mock).mockResolvedValue({ status: "RUNNING" });
    const stop = endpoints.pollDeclarationImport("i1", { onUpdate: vi.fn(), onError: vi.fn(), intervalMs: 5 });

    await vi.waitFor(() => expect((api.get as unknown as Mock).mock.calls.length).toBeGreaterThanOrEqual(2));
    stop();
    const calls = (api.get as unknown as Mock).mock.calls.length;
    await new Promise(resolve => setTimeout(resolve, 20));
    expect(api.get).toHaveBeenCalledTimes(calls);
  });

  it("reports a failed poll through onError", async () => {
    (api.get as unknown as Mock).mockRejectedValue(new Error("offline"));
    const onUpdate = vi.fn();
    const onError = vi.fn();
    const stop = endpoints.pollDeclarationImport("i1", { onUpdate, onError, intervalMs: 5 });

    await vi.waitFor(() => expect(onError).toHaveBeenCalled());
    expect(onUpdate).not.toHaveBeenCalled();
    stop();
  });
});

describe("terminalDeclarationImportStatuses", () => {
  it("contains exactly the statuses that end the polling", () => {
    expect([...endpoints.terminalDeclarationImportStatuses].sort()).toEqual(["APPLIED", "CANCELLED", "FAILED", "NEEDS_REVIEW"]);
  });
});

describe("coverage", () => {
  it("covers every exported function of endpoints.ts", () => {
    const covered = new Set<string>([
      ...callCases.map(c => c.name),
      ...urlCases.map(c => c.name),
      ...uploadCases.map(c => c.name),
      "listTaskAssignees",
      "pollDeclarationImport",
      "terminalDeclarationImportStatuses",
    ]);
    const baseName = (caseName: string) => caseName.replace(/\..*$/, "");
    const isCovered = (label: string) => [...covered].some(name => name === label || name.startsWith(`${label} `));
    for (const [key, value] of Object.entries(endpoints)) {
      if (typeof value === "function") {
        expect(isCovered(key), `uncovered export: ${key}`).toBe(true);
      } else if (value && typeof value === "object" && !(value instanceof Set)) {
        for (const [method, fn] of Object.entries(value)) {
          if (typeof fn === "function") expect(isCovered(`${baseName(key)}.${method}`), `uncovered export: ${key}.${method}`).toBe(true);
        }
      }
    }
  });
});
