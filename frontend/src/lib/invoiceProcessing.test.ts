import { expect, it } from "vitest";
import { INVOICE_PROCESSING_MESSAGES, invoiceProcessingMessage, INVOICE_STATUS_LABELS } from "./invoiceProcessing";

// Every reason an invoice attempt can end with (issue #500); the backend test test_invoice_reason_texts.py keeps this
// list and the map in step with the codes the backend stores.
const MODEL_PATH = ["ai_import_disabled", "ai_invoice_confirmation_missing", "ai_document_model_missing", "ai_key_unreadable",
  "ai_settings_changed", "organization_ai_budget_exceeded", "invoice_page_limit", "invoice_text_limit", "invoice_ai_limit", "model_reading_failed",
  "model_extraction_failed", "provider_unreachable", "model_result_invalid", "lease_lost", "render_timeout", "render_unavailable", "image_pixel_limit"];

it.each(MODEL_PATH)("explains the reason %s in German with what happened and what to do", code => {
  const text = invoiceProcessingMessage("failed", code);
  expect(text).toBe(INVOICE_PROCESSING_MESSAGES[code]);
  expect(text!.length).toBeGreaterThan(60);
});

it("never shows a raw code, an enum value or English text", () => {
  for (const [code, text] of Object.entries(INVOICE_PROCESSING_MESSAGES)) {
    expect(text, code).not.toMatch(/[a-z]+_[a-z_]+/);
    expect(text, code).not.toMatch(/\b(the|and|failed|error|limit exceeded|not found|retry|budget exhausted)\b/i);
    expect(text, code).toMatch(/[äöüß]|\b(nicht|Sie|wurde|ist)\b/);
  }
  for (const label of Object.values(INVOICE_STATUS_LABELS)) expect(label).not.toMatch(/[a-z]+_[a-z_]+/);
});

it("says that the original is kept wherever nothing was applied by a model", () => {
  for (const code of MODEL_PATH) expect(INVOICE_PROCESSING_MESSAGES[code], code).toMatch(/Original|Dokument/);
  for (const code of ["model_reading_failed", "model_extraction_failed", "provider_unreachable", "model_result_invalid", "organization_ai_budget_exceeded"]) {
    expect(INVOICE_PROCESSING_MESSAGES[code], code).toMatch(/nichts übernommen/);
  }
});

it("warns that starting again is paid again only where the models were reached", () => {
  for (const code of ["model_reading_failed", "model_extraction_failed", "model_result_invalid", "lease_lost", "ai_settings_changed"]) {
    expect(INVOICE_PROCESSING_MESSAGES[code], code).toMatch(/verursacht erneut Kosten/);
  }
  for (const code of ["ai_import_disabled", "ai_invoice_confirmation_missing", "invoice_page_limit"]) {
    expect(INVOICE_PROCESSING_MESSAGES[code], code).not.toMatch(/verursacht/);
  }
});

it("says that a provider that could not be reached was sent nothing and cost nothing, and that starting again is no cost by itself", () => {
  const text = INVOICE_PROCESSING_MESSAGES.provider_unreachable;
  expect(text).toMatch(/konnte nicht erreicht werden.*nicht gesendet.*keine Kosten.*Original bleibt erhalten.*erneut starten/s);
  expect(text).not.toMatch(/erneut Kosten/);
});

it("has no explanation while the invoice waits, runs or succeeded, and never a success text for nothing", () => {
  for (const status of ["legacy", "queued", "running", "succeeded"]) expect(invoiceProcessingMessage(status, null)).toBeNull();
});

it("falls back to a German sentence for a code it does not know, never to the code", () => {
  const text = invoiceProcessingMessage("failed", "a_code_nobody_mapped");
  expect(text).toMatch(/^Die Verarbeitung/);
  expect(text).not.toContain("a_code_nobody_mapped");
  expect(invoiceProcessingMessage("needs_review", null)).toMatch(/^Die Verarbeitung|^Die automatische/);
});

it("does not look codes up on the object prototype", () => {
  expect(invoiceProcessingMessage("failed", "constructor")).not.toContain("function");
  expect(invoiceProcessingMessage("failed", "toString")).toMatch(/^Die Verarbeitung/);
});
