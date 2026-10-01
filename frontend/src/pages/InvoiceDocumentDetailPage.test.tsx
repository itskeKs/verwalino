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
    return json({ id: "doc-1", chain_root_id: "doc-1", status: "active", cost_entry_id: null, version: 1, content_byte_size: 100, content_sha256: "a".repeat(64), extraction_payload: { suggested_category_code: "WASSER", gross_amount: "100.00", invoice_date: "2025-06-01", service_period_from: "2025-01-01", service_period_to: "2025-12-31" } });
  }));
  render(<MemoryRouter initialEntries={["/belege/doc-1"]}><Routes>
    <Route path="/belege/:docId" element={<InvoiceDocumentDetailPage />} />
    <Route path="/kosten/:id" element={<p>Saved cost</p>} />
  </Routes></MemoryRouter>);
  fireEvent.click(await screen.findByText("Kostenposition erstellen"));
  fireEvent.change(screen.getByLabelText("Objekt *"), { target: { value: "property-1" } });
  fireEvent.click(screen.getByText("Entwurf erstellen"));
  await screen.findByText("Retry required");
  expect(screen.getByLabelText("Objekt *")).toHaveValue("property-1");
  fireEvent.click(screen.getByText("Entwurf erstellen"));
  await screen.findByText("Saved cost");
  await waitFor(() => expect(attempts).toHaveLength(2));
  expect(JSON.parse(attempts[0].body as string).source_document_id).toBe("doc-1");
  const firstKey = new Headers(attempts[0].headers).get("Idempotency-Key");
  expect(firstKey).toBeTruthy();
  expect(new Headers(attempts[1].headers).get("Idempotency-Key")).toBe(firstKey);
});
