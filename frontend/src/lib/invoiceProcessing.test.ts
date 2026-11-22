import { expect, it } from "vitest";
import { INBOX_PROCESSING_MESSAGES, INVOICE_PROCESSING_MESSAGES, invoiceProcessingMessage, INVOICE_STATUS_LABELS } from "./invoiceProcessing";

// Every reason an invoice attempt can end with (issue #500); the backend test test_invoice_reason_texts.py keeps this
// list and the map in step with the codes the backend stores.
const MODEL_PATH = ["ai_import_disabled", "ai_invoice_confirmation_missing", "ai_document_model_missing", "ai_key_unreadable",
  "ai_settings_changed", "organization_ai_budget_exceeded", "invoice_page_limit", "invoice_text_limit", "invoice_ai_limit", "model_reading_failed",
  "model_extraction_failed", "model_result_invalid", "lease_lost", "render_timeout", "render_unavailable", "image_pixel_limit"];

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
  for (const code of ["model_reading_failed", "model_extraction_failed", "model_result_invalid", "organization_ai_budget_exceeded"]) {
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

// ---- the documents of the inbox (issue #501) ---------------------------------------------------------------------------

// Every reason the inbox's extraction can end with; the backend test test_invoice_reason_texts.py keeps the map in step
// with the codes the backend stores, and the codes below are the ones of the inbox's path.
const INBOX_PATH = ["ai_import_disabled", "ai_inbox_confirmation_missing", "ai_document_model_missing", "ai_key_unreadable", "ai_settings_changed",
  "organization_ai_budget_exceeded", "invoice_page_limit", "invoice_text_limit", "invoice_ai_limit", "structured_selection_partial", "text_size_limit",
  "missing_fields", "model_reading_failed", "model_extraction_failed", "model_result_invalid", "lease_lost", "render_timeout", "render_unavailable",
  "image_pixel_limit", "pdf_parser_unavailable", "file_size_limit", "mime_mismatch", "invalid_page_selection", "source_page_count_changed",
  "unsupported_format", "invalid_document", "extraction_timeout", "processor_failed", "storage_integrity_mismatch", "stored_original_unavailable",
  "initiator_permission_changed", "attempt_limit"];

it.each(INBOX_PATH)("explains the inbox reason %s in German, and not as an invoice", code => {
  const text = invoiceProcessingMessage("failed", code, "inbox");
  expect(text).toBe(INBOX_PROCESSING_MESSAGES[code]);
  expect(text!.length).toBeGreaterThan(60);
  expect(text, code).not.toMatch(/Kostenposition erstellen|Verarbeitung erneut starten|Rechnungsseiten/);
});

it("never shows a raw code, an enum value or English text for a document of the inbox", () => {
  for (const [code, text] of Object.entries(INBOX_PROCESSING_MESSAGES)) {
    expect(text, code).not.toMatch(/[a-z]+_[a-z_]+/);
    expect(text, code).not.toMatch(/\b(the|and|failed|error|limit exceeded|not found|retry|budget exhausted|OCR|Tesseract)\b/i);
    expect(text, code).toMatch(/[äöüß]|\b(nicht|Sie|wurde|ist)\b/);
  }
});

it("says for every reason of the inbox's path what happened to the original and what the user can do", () => {
  for (const code of INBOX_PATH.filter(code => code !== "missing_fields")) expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/nichts übernommen|nicht unverändert bestätigt|nichts ausgewertet/);
  // What the user can do: start again (where the state allows it), settings, a smaller range, or by hand.
  for (const code of ["model_reading_failed", "model_extraction_failed", "model_result_invalid", "lease_lost", "ai_settings_changed", "extraction_timeout", "processor_failed"]) {
    expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/unter „Belege und Vorschläge“ die Auswertung erneut anfordern/);
  }
  for (const code of ["invoice_page_limit", "invoice_text_limit", "invoice_ai_limit"]) expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/kleineren Seitenbereich an/);
  for (const code of ["ai_import_disabled", "ai_inbox_confirmation_missing", "ai_document_model_missing", "ai_key_unreadable", "organization_ai_budget_exceeded"]) {
    expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/Ihre Administration (kann|muss)/);
  }
  expect(INBOX_PROCESSING_MESSAGES.attempt_limit).toMatch(/dreimal versucht/);
});

it("tells whether anything was sent: not before the first request, and where the usage stands once requests were made", () => {
  for (const code of ["ai_import_disabled", "ai_inbox_confirmation_missing", "ai_document_model_missing", "ai_key_unreadable", "ai_settings_changed"]) {
    expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/Es wird nichts \(mehr\) an einen KI-Anbieter übermittelt.*Verbrauch der KI-Verarbeitung/s);
  }
  for (const code of ["invoice_page_limit", "structured_selection_partial"]) expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/(nicht|nichts) an einen KI-Anbieter übermittelt/);
  for (const code of ["model_reading_failed", "model_extraction_failed", "model_result_invalid", "invoice_text_limit", "invoice_ai_limit", "organization_ai_budget_exceeded"]) {
    expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/Verbrauch der KI-Verarbeitung/);
  }
  for (const code of ["model_reading_failed", "model_extraction_failed", "model_result_invalid", "ai_settings_changed"]) expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/verursacht erneut Kosten/);
  for (const code of ["ai_import_disabled", "invoice_page_limit", "ai_inbox_confirmation_missing"]) expect(INBOX_PROCESSING_MESSAGES[code], code).not.toMatch(/verursacht/);
});

it("names the inbox documents the switch covers and no kind of document that depends on the installation (invoices are processed only on some)", () => {
  const text = INBOX_PROCESSING_MESSAGES.ai_import_disabled;
  expect(text).toMatch(/Schalter einschalten, der auch die Dokumente im Posteingang auswertet/);
  expect(text).not.toMatch(/Rechnung|Teilungserkl/);
  expect(INVOICE_PROCESSING_MESSAGES.ai_import_disabled).toMatch(/Teilungserklärungen und Rechnungen/);
});

it("keeps the invoice wording for an invoice and has no inbox code in an invoice's wording of the shared reasons", () => {
  expect(invoiceProcessingMessage("failed", "model_extraction_failed")).toBe(INVOICE_PROCESSING_MESSAGES.model_extraction_failed);
  expect(INVOICE_PROCESSING_MESSAGES.model_extraction_failed).toMatch(/Kostenposition erstellen/);
  expect(INBOX_PROCESSING_MESSAGES.model_extraction_failed).not.toBe(INVOICE_PROCESSING_MESSAGES.model_extraction_failed);
  expect(INVOICE_PROCESSING_MESSAGES.ai_inbox_confirmation_missing).toBe(INBOX_PROCESSING_MESSAGES.ai_inbox_confirmation_missing);
});

it("names the inbox documents that the confirmation text must cover, and shows the earlier text recognition only as history", () => {
  expect(INBOX_PROCESSING_MESSAGES.ai_inbox_confirmation_missing).toMatch(/Dokumente im Posteingang an den KI-Anbieter übermittelt werden dürfen.*ohne den Schlüssel erneut einzugeben/s);
  for (const code of ["ocr_unavailable", "ocr_timeout", "ocr_page_limit", "incomplete_text"]) expect(INBOX_PROCESSING_MESSAGES[code], code).toMatch(/^Die frühere Texterkennung konnte diesen Beleg nicht auswerten/);
});

it("has no explanation while a document of the inbox waits, runs or succeeded, and a German sentence for what nothing explains", () => {
  for (const status of ["queued", "running", "succeeded"]) expect(invoiceProcessingMessage(status, null, "inbox")).toBeNull();
  const text = invoiceProcessingMessage("failed", "a_code_nobody_mapped", "inbox");
  expect(text).toMatch(/^Die Auswertung konnte nicht abgeschlossen werden\. Das Original bleibt erhalten/);
  expect(text).not.toContain("a_code_nobody_mapped");
  expect(invoiceProcessingMessage("failed", "constructor", "inbox")).toMatch(/^Die Auswertung/);
});
