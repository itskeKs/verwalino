import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { InvoiceDocumentDetailPage } from "./InvoiceDocumentDetailPage";
import { api } from "../api/client";
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
  fireEvent.click(screen.getByText("Texterkennung erneut starten"));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Gespeichert"));
  expect(fetchMock.mock.calls.some(([url]) => url === "/api/cost-entries")).toBe(false);
});

afterEach(() => vi.restoreAllMocks());

it.each(["Objekt *", "Kategorie *", "Umlagefaehigkeit *"])("explicitly labels the active invoice draft's %s without including options", async name => {
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
  await screen.findByRole("option", { name: "WEG Bereich · WEG · 2026-01-01 – offen" });
  fireEvent.change(screen.getByRole("combobox", { name: "Abrechnungskreis" }), { target: { value: "scope-weg" } });
  fireEvent.change(screen.getByRole("combobox", { name: "Kategorie *" }), { target: { value: "cat-water" } });
  fireEvent.change(screen.getByLabelText("Rechnungsdatum *", { exact: true }), { target: { value: "2026-07-01" } });
  fireEvent.change(screen.getByLabelText("Leistungszeitraum von *", { exact: true }), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("Leistungszeitraum bis *", { exact: true }), { target: { value: "2026-12-31" } });
  fireEvent.change(screen.getByLabelText("Bruttobetrag *", { exact: true }), { target: { value: "240.00" } });
}

it.each([
  ["Voll umlagefaehig", "UMLAGEFAEHIG"],
  ["Teilweise umlagefaehig", "TEILWEISE_PRUEFEN"],
  ["Nicht umlagefaehig", "NICHT_UMLAGEFAEHIG"],
])("submits canonical apportionability for the visible choice %s", async (label, canonical) => {
  const create = await openInvoiceCostForPayload();
  await fillInvoiceCostForm();
  const option = screen.getByRole("option", { name: label }) as HTMLOptionElement;
  fireEvent.change(screen.getByRole("combobox", { name: "Umlagefaehigkeit *" }), { target: { value: option.value } });
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
  ["Voll umlagefaehig", "UMLAGEFAEHIG"],
  ["Teilweise umlagefaehig", "TEILWEISE_PRUEFEN"],
  ["Nicht umlagefaehig", "NICHT_UMLAGEFAEHIG"],
])("keeps the category default %s selected and submits it without manual apportionability changes", async (label, canonical) => {
  const create = await openInvoiceCostForPayload(canonical);
  const select = screen.getByRole("combobox", { name: "Umlagefaehigkeit *" });
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
