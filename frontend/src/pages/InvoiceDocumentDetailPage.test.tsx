import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { InvoiceDocumentDetailPage } from "./InvoiceDocumentDetailPage";

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
