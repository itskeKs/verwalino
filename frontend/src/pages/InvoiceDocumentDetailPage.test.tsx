import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { InvoiceDocumentDetailPage } from "./InvoiceDocumentDetailPage";
import { api, ApiError } from "../api/client";
import * as endpoints from "../api/endpoints";
import type { CostCategory, InvoiceDocument, Property } from "../api/types";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("submits the source and retains the same key and form after uncertain failure", async () => {
  const attempts: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/cost-entries") {
      attempts.push(init!);
      return attempts.length === 1 ? json({ detail: "Retry required" }, 503) : json({ id: "cost-1" }, 201);
    }
    if (url.endsWith("/model-usage")) return json({ stages: [], total: null });
    if (url === "/api/cost-categories") return json([{ id: "cat-1", code: "WASSER", label: "Wasser", plan_version: 1, tenant_apportionability_default: "UMLAGEFAEHIG" }]);
    if (url === "/api/properties") return json([{ id: "property-1", name: "Testhaus" }]);
    if (url.startsWith("/api/accounting-scopes/page")) return json({ items: [{ id: "scope-1", label: "Mietbereich", scope_type: "MIET", valid_from: "2025-01-01", valid_to: null }], next_cursor: null });
    return json({ id: "doc-1", chain_root_id: "doc-1", status: "active", cost_entry_id: null, version: 1, content_byte_size: 100, content_sha256: "a".repeat(64), extraction_payload: { suggested_category_code: "WASSER", gross_amount: "100.00", invoice_date: "2025-06-01", service_period_from: "2025-01-01", service_period_to: "2025-12-31" } });
  }));
  render(<MemoryRouter initialEntries={["/belege/doc-1"]}><Routes>
    <Route path="/belege/:docId" element={<InvoiceDocumentDetailPage />} />
    <Route path="/kosten/:id" element={<p>Saved cost</p>} />
  </Routes></MemoryRouter>);
  fireEvent.click(await screen.findByText("Kostenposition erstellen"));
  fireEvent.change(screen.getByLabelText("Objekt *"), { target: { value: "property-1" } });
  await screen.findByRole("option", { name: /Mietbereich/ });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope-1" } });
  fireEvent.click(screen.getByText("Entwurf erstellen"));
  await screen.findByText("Retry required");
  expect(screen.getByRole("combobox", { name: "Abrechnungskreis" })).toHaveValue("scope-1");
  expect(screen.getByLabelText("Objekt *")).toHaveValue("property-1");
  fireEvent.click(screen.getByText("Entwurf erstellen"));
  await screen.findByText("Saved cost");
  await waitFor(() => expect(attempts).toHaveLength(2));
  expect(JSON.parse(attempts[0].body as string).source_document_id).toBe("doc-1");
  expect(JSON.parse(attempts[0].body as string).accounting_scope_id).toBe("scope-1");
  const firstKey = new Headers(attempts[0].headers).get("Idempotency-Key");
  expect(firstKey).toBeTruthy();
  expect(new Headers(attempts[1].headers).get("Idempotency-Key")).toBe(firstKey);
});

it("shows a failed extraction as stored original and queues a retry without a cost", async () => {
  let retried = false;
  const fetchMock = vi.fn(async (url: string) => {
    if (url === "/api/cost-categories" || url === "/api/properties") return json([]);
    if (url.endsWith("/model-usage")) return json({ stages: [], total: null });
    if (url.endsWith("/retry")) retried = true;
    return json({ id: "doc-1", chain_root_id: "doc-1", status: "active", released_at: null,
      cost_entry_id: null, version: 1, content_byte_size: 100, content_sha256: "a".repeat(64),
      processing_status: retried ? "queued" : "timeout", processing_error_code: "ocr_timeout", extraction_payload: null });
  });
  vi.stubGlobal("fetch", fetchMock);
  render(<MemoryRouter initialEntries={["/belege/doc-1"]}><Routes>
    <Route path="/belege/:docId" element={<InvoiceDocumentDetailPage />} />
  </Routes></MemoryRouter>);
  expect(await screen.findByRole("status")).toHaveTextContent("Zeitlimit erreicht");
  expect(screen.getByText("Dokument ansehen")).toHaveAttribute("href", "/api/invoice-documents/doc-1/content");
  fireEvent.click(screen.getByText("Verarbeitung erneut starten"));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Gespeichert"));
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/cost-entries")).toBe(false);
});

afterEach(() => vi.restoreAllMocks());

it.each(["Objekt *", "Kategorie *", "Umlagefähigkeit *"])("explicitly labels the active invoice draft's %s without including options", async name => {
  const document: InvoiceDocument = {
    id: "doc-label", organization_id: "org", cost_entry_id: null, storage_backend: "local",
    original_filename: "Rechnung.pdf", mime_type: "application/pdf", content_byte_size: 100,
    content_sha256: "a".repeat(64), version: 1, supersedes_id: null, chain_root_id: "doc-label",
    status: "active", released_at: null, released_by: null, retention_until: null, superseded_at: null,
    processing_status: "succeeded", processing_error_code: null,
    extraction_payload: {
      vendor_name: "Wasserwerk", invoice_date: "2026-06-30", document_number: "R-2026-1", gross_amount: "100.00",
      vat_amount: null, vat_rate: null, service_period_from: "2026-01-01", service_period_to: "2026-12-31",
      suggested_category_code: "WASSER", confidence: 0.9, field_confidences: {}, raw_text: "Wasserrechnung",
    },
  };
  const property: Property = { id: "property-weg", name: "WEG Haus", street: "Weg 1", zip_code: "10115", city: "Berlin", management_type: "WEG", fiscal_year_start_month: 1, mea_denominator: 1000, mea_scale_status: "CONFIRMED" };
  const category: CostCategory = { id: "cat-water", plan_version: 1, code: "WASSER", label: "Wasser", cost_group: "BEWIRTSCHAFTUNG", tenant_apportionability_default: "UMLAGEFAEHIG", owner_billing_relevant_default: true, owner_distribution_key_default: "MEA", tenant_distribution_key_default: null, reserve_relevant: false, legal_hint: null, sort_order: 1, is_active: true };
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/invoice-documents/doc-label") return document as never;
    if (path.endsWith("/model-usage")) return { stages: [], total: null } as never;
    if (path === "/api/cost-categories") return [category] as never;
    if (path === "/api/properties") return [property] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [], next_cursor: null } as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  render(<MemoryRouter initialEntries={["/belege/doc-label"]}><Routes>
    <Route path="/belege/:docId" element={<InvoiceDocumentDetailPage />} />
  </Routes></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Kostenposition erstellen" }));
  const select = screen.getByRole("combobox", { name });
  expect(select).toHaveAttribute("aria-label", name);
  expect(select).toHaveAccessibleName(name);
  const choice = name === "Objekt *" ? property.id : name === "Kategorie *" ? category.id : "TEILWEISE_PRUEFEN";
  fireEvent.change(select, { target: { value: choice } });
  expect(select).toHaveValue(choice);
});

async function openInvoiceCostForPayload(categoryDefault = "UMLAGEFAEHIG") {
  const document: InvoiceDocument = {
    id: "doc-payload", organization_id: "org", cost_entry_id: null, storage_backend: "LOCAL_FS",
    original_filename: "Rechnung.pdf", mime_type: "application/pdf", content_byte_size: 100,
    content_sha256: "a".repeat(64), version: 1, supersedes_id: null, chain_root_id: "doc-payload",
    status: "active", released_at: null, released_by: null, retention_until: null, superseded_at: null,
    processing_status: "succeeded", processing_error_code: null,
    extraction_payload: {
      vendor_name: "Wasserwerk", invoice_date: "2026-06-30", document_number: "R-2026-1", gross_amount: "100.00",
      vat_amount: null, vat_rate: null, service_period_from: "2026-01-01", service_period_to: "2026-12-31",
      suggested_category_code: "WASSER", confidence: 0.9, field_confidences: {}, raw_text: "Wasserrechnung",
    },
  };
  const property: Property = { id: "property-weg", name: "WEG Haus", street: "Weg 1", zip_code: "10115", city: "Berlin", management_type: "WEG", fiscal_year_start_month: 1, mea_denominator: 1000, mea_scale_status: "CONFIRMED" };
  const category: CostCategory = { id: "cat-water", plan_version: 1, code: "WASSER", label: "Wasser", cost_group: "UMLAGEFAEHIG", tenant_apportionability_default: categoryDefault, owner_billing_relevant_default: true, owner_distribution_key_default: "MEA", tenant_distribution_key_default: null, reserve_relevant: false, legal_hint: null, sort_order: 1, is_active: true };
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === "/api/invoice-documents/doc-payload") return document as never;
    if (path.endsWith("/model-usage")) return { stages: [], total: null } as never;
    if (path === "/api/cost-categories") return [category] as never;
    if (path === "/api/properties") return [property] as never;
    if (path.startsWith("/api/accounting-scopes/page?")) return { items: [{
      id: "scope-weg", property_id: property.id, label: "WEG Bereich", scope_type: "WEG", valid_from: "2026-01-01", valid_to: null,
    }], next_cursor: null } as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  const create = vi.spyOn(endpoints, "createCostEntry").mockResolvedValue({ id: "cost-created" } as never);
  render(<MemoryRouter initialEntries={["/belege/doc-payload"]}><Routes>
    <Route path="/belege/:docId" element={<InvoiceDocumentDetailPage />} />
    <Route path="/kosten/:id" element={<p>Saved invoice cost</p>} />
  </Routes></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Kostenposition erstellen" }));
  return create;
}

async function fillInvoiceCostForm() {
  fireEvent.change(screen.getByRole("combobox", { name: "Objekt *" }), { target: { value: "property-weg" } });
  await screen.findByRole("option", { name: "WEG Bereich · WEG · 01.01.2026 – offen" });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope-weg" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Kategorie *" }), { target: { value: "cat-water" } });
  fireEvent.change(screen.getByLabelText("Rechnungsdatum *", { exact: true }), { target: { value: "2026-07-01" } });
  fireEvent.change(screen.getByLabelText("Leistungszeitraum von *", { exact: true }), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("Leistungszeitraum bis *", { exact: true }), { target: { value: "2026-12-31" } });
  fireEvent.change(screen.getByLabelText("Bruttobetrag *", { exact: true }), { target: { value: "240.00" } });
}

it.each([
  ["Voll umlagefähig", "UMLAGEFAEHIG"],
  ["Teilweise umlagefähig", "TEILWEISE_PRUEFEN"],
  ["Nicht umlagefähig", "NICHT_UMLAGEFAEHIG"],
])("submits canonical apportionability for the visible choice %s", async (label, canonical) => {
  const create = await openInvoiceCostForPayload();
  await fillInvoiceCostForm();
  const option = screen.getByRole("option", { name: label }) as HTMLOptionElement;
  fireEvent.change(screen.getByRole("combobox", { name: "Umlagefähigkeit *" }), { target: { value: option.value } });
  fireEvent.click(screen.getByRole("button", { name: "Entwurf erstellen" }));
  await screen.findByText("Saved invoice cost");
  expect(create).toHaveBeenCalledTimes(1);
  expect(create.mock.calls[0][0]).toEqual({
    property_id: "property-weg", accounting_scope_id: "scope-weg", cost_category_id: "cat-water", cost_category_plan_version: 1,
    vendor_id: null, invoice_date: "2026-07-01", service_period_from: "2026-01-01", service_period_to: "2026-12-31",
    gross_amount: "240.00", vat_amount: null, vat_rate: null, document_number: "R-2026-1",
    owner_distribution_key: "MEA", tenant_distribution_key: null, tenant_apportionable: canonical,
    apportionable_tenant_amount: null, source: "PDF_IMPORT", status: "draft_extracted", source_document_id: "doc-payload",
  });
  expect(create.mock.calls[0][1]).toBeTruthy();
});

it.each([
  ["Voll umlagefähig", "UMLAGEFAEHIG"],
  ["Teilweise umlagefähig", "TEILWEISE_PRUEFEN"],
  ["Nicht umlagefähig", "NICHT_UMLAGEFAEHIG"],
])("keeps the category default %s selected and submits it without manual apportionability changes", async (label, canonical) => {
  const create = await openInvoiceCostForPayload(canonical);
  const select = screen.getByRole("combobox", { name: "Umlagefähigkeit *" });
  expect(select).toHaveValue(canonical);
  expect((screen.getByRole("option", { name: label }) as HTMLOptionElement).selected).toBe(true);
  await fillInvoiceCostForm();
  expect(select).toHaveValue(canonical);
  fireEvent.click(screen.getByRole("button", { name: "Entwurf erstellen" }));
  await screen.findByText("Saved invoice cost");
  expect(create).toHaveBeenCalledTimes(1);
  expect(create.mock.calls[0][0]).toMatchObject({
    tenant_apportionable: canonical, cost_category_id: "cat-water", accounting_scope_id: "scope-weg",
    source: "PDF_IMPORT", status: "draft_extracted", source_document_id: "doc-payload",
  });
});


// ---- what the screen says when the model-assisted processing did not produce a proposal (issue #500) ----

function failedInvoice(status: string, code: string | null, over: object = {}): InvoiceDocument {
  return { id: "doc-out", organization_id: "org", cost_entry_id: null, storage_backend: "LOCAL_FS", original_filename: "Scan.pdf", mime_type: "application/pdf",
    content_byte_size: 100, content_sha256: "a".repeat(64), version: 1, supersedes_id: null, chain_root_id: "doc-out", status: "active", released_at: null,
    released_by: null, retention_until: null, superseded_at: null, processing_status: status, processing_error_code: code, extraction_payload: null, ...over };
}

function openInvoice(document: InvoiceDocument, usage: unknown = { stages: [], total: null }, categories: unknown[] = []) {
  vi.spyOn(api, "get").mockImplementation(async path => {
    if (path === `/api/invoice-documents/${document.id}`) return document as never;
    if (path === `/api/invoice-documents/${document.id}/model-usage`) return usage as never;
    if (path === "/api/cost-categories") return categories as never;
    if (path === "/api/properties") return [] as never;
    throw new Error(`Unexpected GET ${path}`);
  });
  render(<MemoryRouter initialEntries={[`/belege/${document.id}`]}><Routes>
    <Route path="/belege/:docId" element={<InvoiceDocumentDetailPage />} />
  </Routes></MemoryRouter>);
}

it.each([
  ["unavailable", "ai_import_disabled", /nicht eingerichtet.*nichts \(mehr\) an einen KI-Anbieter übermittelt.*Verbrauch der KI-Verarbeitung.*Original bleibt erhalten/s],
  ["unavailable", "ai_invoice_confirmation_missing", /noch nicht bestätigt.*Rechnungen an den KI-Anbieter/s],
  ["unavailable", "ai_settings_changed", /Einstellungen.*geändert.*nichts \(mehr\) an einen KI-Anbieter übermittelt.*Verbrauch der KI-Verarbeitung.*nichts übernommen.*aktuellen Einstellungen.*erneut Kosten/s],
  ["unavailable", "organization_ai_budget_exceeded", /monatliche KI-Budget.*ausgeschöpft.*Verbrauch der KI-Verarbeitung/s],
  ["failed", "model_reading_failed", /Dokumente einlesen.*nichts übernommen.*erneut Kosten/s],
  ["failed", "model_extraction_failed", /Informationen extrahieren.*nichts übernommen.*erneut Kosten/s],
  ["failed", "provider_unreachable", /Anbieter.*nicht erreicht werden.*nicht gesendet.*keine Kosten.*nichts übernommen.*erneut starten/s],
  ["canceled", "source_no_longer_editable", /freigegeben oder durch eine neue Version ersetzt/],
  ["timeout", "worker_lease_expired", /unterbrochen/],
])("explains %s / %s on the invoice, keeps the original downloadable and shows no code", async (status, code, expected) => {
  openInvoice(failedInvoice(status, code));
  const text = await screen.findByText(expected);
  expect(text).toBeVisible();
  expect(document.body).not.toHaveTextContent(code);
  expect(screen.getByText("Dokument ansehen")).toHaveAttribute("href", "/api/invoice-documents/doc-out/content");
  expect(screen.queryByText(/Extrahierte Felder/)).not.toBeInTheDocument();
});

it("shows no usage card for an attempt whose requests never left the host, and offers to start again", async () => {
  openInvoice(failedInvoice("failed", "provider_unreachable"));
  await screen.findByText(/nicht erreicht werden/);
  expect(screen.queryByRole("heading", { name: "Verbrauch der KI-Verarbeitung" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Verarbeitung erneut starten" })).toBeVisible();
});

it("offers to start again after a cancellation caused by a lost permission, and not after a replaced original", async () => {
  openInvoice(failedInvoice("canceled", "scope_permission_changed"));
  expect(await screen.findByRole("button", { name: "Verarbeitung erneut starten" })).toBeVisible();
  cleanup();
  openInvoice(failedInvoice("canceled", "source_no_longer_editable", { status: "superseded" }));
  await screen.findByText(/durch eine neue Version ersetzt/);
  expect(screen.queryByRole("button", { name: "Verarbeitung erneut starten" })).not.toBeInTheDocument();
});

it("offers to start again after the AI settings were changed during the processing", async () => {
  openInvoice(failedInvoice("unavailable", "ai_settings_changed"));
  expect(await screen.findByRole("button", { name: "Verarbeitung erneut starten" })).toBeVisible();
});

it("says nothing about a problem while the invoice waits and reports nothing as done", async () => {
  openInvoice(failedInvoice("queued", null));
  expect(await screen.findByRole("status")).toHaveTextContent("wartet auf Verarbeitung");
  expect(screen.queryByText(/Original bleibt erhalten/)).not.toBeInTheDocument();
  expect(screen.queryByText(/Verbrauch der KI-Verarbeitung/)).not.toBeInTheDocument();
});

it("shows the usage of a failed attempt on the invoice", async () => {
  openInvoice(failedInvoice("failed", "model_extraction_failed"), {
    stages: [{ stage: "READING", model: "gpt-5.4-mini", calls: 1, usage_status: "REPORTED", reported_input_tokens: 1000, reported_output_tokens: 200, input_tokens: 1000, output_tokens: 200, estimated_cost_usd: "0.00165" }],
    total: { usage_status: "REPORTED", reported_input_tokens: 1000, reported_output_tokens: 200, input_tokens: 1000, output_tokens: 200, estimated_cost_usd: "0.00165" },
  });
  expect(await screen.findByRole("heading", { name: "Verbrauch der KI-Verarbeitung" })).toBeVisible();
  expect(screen.getAllByText("0,00165 USD").length).toBe(2);
});

it("explains in German when starting again is refused, not with the server's English message", async () => {
  openInvoice(failedInvoice("failed", "model_result_invalid"));
  vi.spyOn(api, "post").mockRejectedValue(new ApiError(409, "Extraction retry budget exhausted; review the original manually"));
  fireEvent.click(await screen.findByRole("button", { name: "Verarbeitung erneut starten" }));
  expect(await screen.findByText(/schon dreimal versucht/)).toBeVisible();
  expect(document.body).not.toHaveTextContent("retry budget");
});

it("shows no extracted fields for the empty stub of an upload, and the fields of a proposal an attempt made", async () => {
  const stub = { vendor_name: null, invoice_date: null, document_number: null, gross_amount: null, vat_amount: null, vat_rate: null,
    service_period_from: null, service_period_to: null, suggested_category_code: null, confidence: 0, field_confidences: {}, raw_text: null, is_einvoice: false, source_format: null };
  openInvoice(failedInvoice("unavailable", "ai_import_disabled", { extraction_payload: stub }));
  await screen.findByText(/nicht eingerichtet/);
  expect(screen.queryByText("Extrahierte Felder")).not.toBeInTheDocument();
  expect(screen.queryByText("nicht erkannt")).not.toBeInTheDocument();
  cleanup();
  openInvoice(failedInvoice("needs_review", "missing_fields", { extraction_payload: { ...stub, attempt_id: "attempt-1", vendor_name: "Mustermann Versicherungen AG" } }));
  expect(await screen.findByText("Extrahierte Felder")).toBeVisible();
});

/** The row of the card „Extrahierte Felder“ with this label: its term and its value. */
const fieldRow = async (label: string) => (await screen.findByText(label, { selector: "dt" })).closest("div")!;
const fieldValue = async (label: string) => (await fieldRow(label)).querySelector("dd")!.textContent;

const proposalFields = { vendor_name: "Musterversicherung AG", invoice_date: "2026-01-15", document_number: "RE-2026-0042", gross_amount: "312.00",
  vat_amount: null, vat_rate: null, service_period_from: "2026-01-01", service_period_to: "2026-12-31", suggested_category_code: null,
  confidence: 0.84, raw_text: "Rechnung", is_einvoice: false, source_format: null, attempt_id: "attempt-1" };

it("shows the issuer's IBAN grouped in blocks of four, with the certainty of the model, for review", async () => {
  openInvoice(failedInvoice("succeeded", null, { extraction_payload: { ...proposalFields, vendor_iban: "DE02120300000000202051", field_confidences: { gross_amount: 0.98, vendor_iban: 0.9 } } }));
  const row = await fieldRow("IBAN des Rechnungsstellers");
  expect(row).toHaveTextContent("DE02 1203 0000 0000 2020 51");
  expect(row).toHaveTextContent("(90%)");
});

it.each([
  ["a proposal that states none", { vendor_iban: null, field_confidences: { vendor_iban: 0 } }],
  ["a proposal stored before the field existed", { field_confidences: {} }],
])("says that the IBAN is not recognised for %s, as it does for every other field", async (_name, extra) => {
  openInvoice(failedInvoice("succeeded", null, { extraction_payload: { ...proposalFields, ...extra } }));
  const row = await fieldRow("IBAN des Rechnungsstellers");
  expect(row).toHaveTextContent("nicht erkannt");
  expect(row).not.toHaveTextContent("%");
});

it("shows the IBAN of a structured invoice with the certainty of its parser", async () => {
  openInvoice(failedInvoice("succeeded", null, { extraction_payload: { ...proposalFields, is_einvoice: true, source_format: "XRECHNUNG_UBL",
    vendor_iban: "DE02120300000000202051", field_confidences: { vendor_iban: 0.99 } } }));
  const row = await fieldRow("IBAN des Rechnungsstellers");
  expect(row).toHaveTextContent("DE02 1203 0000 0000 2020 51");
  expect(row).toHaveTextContent("(99%)");
});

const insurance = { id: "cat-ins", plan_version: 1, code: "GEBAEUDEVERSICHERUNG", label: "Gebaeudeversicherung", cost_group: "UMLAGEFAEHIG", tenant_apportionability_default: "UMLAGEFAEHIG",
  owner_billing_relevant_default: true, owner_distribution_key_default: "MEA", tenant_distribution_key_default: "WOHNFLAECHE", reserve_relevant: false, legal_hint: null, sort_order: 130, is_active: true };

it("shows the proposal in the display formats of the project, not as stored: dates, amounts, rate and the label of the category", async () => {
  openInvoice(failedInvoice("succeeded", null, { extraction_payload: { ...proposalFields, vat_amount: "49.82", vat_rate: "19.00", suggested_category_code: "GEBAEUDEVERSICHERUNG",
    service_period_to: "2026-12-31", field_confidences: {} } }), undefined, [insurance]);
  expect(await fieldValue("Lieferant")).toBe("Musterversicherung AG");
  expect(await fieldValue("Rechnungsdatum")).toBe("15.01.2026");
  expect(await fieldValue("Belegnummer")).toBe("RE-2026-0042");
  expect(await fieldValue("Bruttobetrag")).toBe("312,00 €");
  expect(await fieldValue("USt-Betrag")).toBe("49,82 €");
  expect(await fieldValue("USt-Satz")).toBe("19 %");
  expect(await fieldValue("Leistungszeitraum von")).toBe("01.01.2026");
  expect(await fieldValue("Leistungszeitraum bis")).toBe("31.12.2026");
  expect(await fieldValue("Kategorievorschlag")).toBe("Gebaeudeversicherung");
});

it("keeps the certainty of a formatted value beside it", async () => {
  openInvoice(failedInvoice("succeeded", null, { extraction_payload: { ...proposalFields, field_confidences: { gross_amount: 0.98, invoice_date: 0.97 } } }));
  expect(await fieldValue("Bruttobetrag")).toBe("312,00 €(98%)");
  expect(await fieldValue("Rechnungsdatum")).toBe("15.01.2026(97%)");
});

it("shows what an older proposal stored as stored, never as an invalid date or NaN", async () => {
  openInvoice(failedInvoice("succeeded", null, { extraction_payload: { ...proposalFields, invoice_date: "15.01.2026", gross_amount: "312,00 EUR", vat_amount: "n/a",
    vat_rate: "19 Prozent", service_period_from: "nach Vereinbarung", service_period_to: "", suggested_category_code: "UNBEKANNT", field_confidences: {} } }), undefined, [insurance]);
  expect(await fieldValue("Rechnungsdatum")).toBe("15.01.2026");
  expect(await fieldValue("Bruttobetrag")).toBe("312,00 EUR");
  expect(await fieldValue("USt-Betrag")).toBe("n/a");
  expect(await fieldValue("USt-Satz")).toBe("19 Prozent");
  expect(await fieldValue("Leistungszeitraum von")).toBe("nach Vereinbarung");
  expect(await fieldValue("Leistungszeitraum bis")).toBe("");
  expect(await fieldValue("Kategorievorschlag")).toBe("UNBEKANNT");
  expect(document.body).not.toHaveTextContent(/Invalid Date|NaN|undefined/);
});

it("says that nothing was recognised in the row of every field the proposal leaves empty", async () => {
  openInvoice(failedInvoice("needs_review", "missing_fields", { extraction_payload: { ...proposalFields, vendor_name: null, invoice_date: null, document_number: null, gross_amount: null,
    service_period_from: null, service_period_to: null, vendor_iban: null, field_confidences: {} } }));
  for (const label of ["Lieferant", "Rechnungsdatum", "Belegnummer", "Bruttobetrag", "USt-Betrag", "USt-Satz", "Leistungszeitraum von", "Leistungszeitraum bis", "Kategorievorschlag", "IBAN des Rechnungsstellers"])
    expect(await fieldValue(label)).toBe("nicht erkannt");
});
