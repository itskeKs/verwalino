import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import type { InvoiceModelUsage as Usage, InvoiceUsageStage } from "../api/types";
import { InvoiceModelUsage } from "./InvoiceModelUsage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const stage = (over: Partial<InvoiceUsageStage>): InvoiceUsageStage => ({ stage: "READING", model: "gpt-5.4-mini", calls: 1, usage_status: "REPORTED", reported_input_tokens: 1000,
  reported_output_tokens: 200, input_tokens: 1000, output_tokens: 200, estimated_cost_usd: "0.00165", ...over });

function show(usage: Usage, status = "succeeded") {
  vi.spyOn(api, "get").mockResolvedValue(usage as never);
  return render(<InvoiceModelUsage documentId="doc-1" processingStatus={status} />);
}

it("shows nothing before a request was made, and no cost of zero", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ stages: [], total: null } as never);
  const { container } = render(<InvoiceModelUsage documentId="doc-1" processingStatus="queued" />);
  await waitFor(() => expect(get).toHaveBeenCalledWith("/api/invoice-documents/doc-1/model-usage"));
  expect(container).toBeEmptyDOMElement();
});

it("lists reading and extraction with model, tokens and cost, and the total", async () => {
  show({ stages: [stage({ calls: 2, reported_input_tokens: 2200, input_tokens: 2200, reported_output_tokens: 500, output_tokens: 500, estimated_cost_usd: "0.00365" }),
      stage({ stage: "EXTRACTION", model: "gpt-5.6-luna", reported_input_tokens: 2000, input_tokens: 2000, reported_output_tokens: 100, output_tokens: 100, estimated_cost_usd: "0.00085" })],
    total: { usage_status: "REPORTED", reported_input_tokens: 4200, reported_output_tokens: 600, input_tokens: 4200, output_tokens: 600, estimated_cost_usd: "0.0045" } });
  expect(await screen.findByRole("heading", { name: "Verbrauch der KI-Verarbeitung" })).toBeVisible();
  const reading = screen.getByRole("group", { name: "Dokumente einlesen" });
  expect(within(reading).getByText("gpt-5.4-mini")).toBeVisible();
  expect(within(reading).getByText("2 Aufrufe")).toBeVisible();
  expect(within(reading).getByText("0,00365 USD")).toBeVisible();
  const extraction = screen.getByRole("group", { name: "Informationen extrahieren" });
  expect(within(extraction).getByText("gpt-5.6-luna")).toBeVisible();
  expect(within(extraction).getByText("0,00085 USD")).toBeVisible();
  const total = screen.getByRole("group", { name: "Gesamt" });
  expect(within(total).getByText("4.200")).toBeVisible();
  expect(within(total).getByText("0,0045 USD")).toBeVisible();
});

it("states unreported usage as a reservation that stays in the budget, never as consumed or free", async () => {
  show({ stages: [stage({ usage_status: "RESERVED", reported_input_tokens: null, reported_output_tokens: null, input_tokens: 1000, output_tokens: 6000, estimated_cost_usd: "0.027013" })],
    total: { usage_status: "RESERVED", reported_input_tokens: null, reported_output_tokens: null, input_tokens: 1000, output_tokens: 6000, estimated_cost_usd: "0.027013" } }, "failed");
  expect((await screen.findAllByText("Reservierte Eingabetoken")).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/Der tatsächliche Tokenverbrauch ist noch nicht bekannt/).length).toBeGreaterThan(0);
  expect(screen.getAllByText("Konservative Kostenschätzung").length).toBeGreaterThan(0);
  expect(screen.queryByText("Eingabetoken", { exact: true })).not.toBeInTheDocument();
});

it("shows a model without a price as having no known price, never as 0,00 USD", async () => {
  show({ stages: [stage({ model: "local-vision", estimated_cost_usd: null })],
    total: { usage_status: "REPORTED", reported_input_tokens: 1000, reported_output_tokens: 200, input_tokens: 1000, output_tokens: 200, estimated_cost_usd: null } });
  expect((await screen.findAllByText("kein Preis bekannt")).length).toBe(2);
  expect(screen.queryByText(/0[,.]00.*USD/)).not.toBeInTheDocument();
});

it("keeps what the reported part cost apart from the conservative total when only some usage was reported", async () => {
  show({ stages: [stage({ usage_status: "PARTIAL", calls: 2, reported_input_tokens: 1000, reported_output_tokens: 200, input_tokens: 5000, output_tokens: 6200, estimated_cost_usd: "0.03165" })],
    total: { usage_status: "PARTIAL", reported_input_tokens: 1000, reported_output_tokens: 200, input_tokens: 5000, output_tokens: 6200, estimated_cost_usd: "0.03165" } });
  expect((await screen.findAllByText("Gemeldete Eingabetoken")).length).toBeGreaterThan(0);
  expect(screen.getAllByText("Konservativ erfasste Eingabetoken").length).toBeGreaterThan(0);
});

it("says so when the usage cannot be loaded and shows no amount", async () => {
  vi.spyOn(api, "get").mockRejectedValue(new ApiError(500, "boom"));
  render(<InvoiceModelUsage documentId="doc-1" processingStatus="failed" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Der Verbrauch der KI-Verarbeitung konnte nicht geladen werden.");
  expect(screen.queryByText(/USD/)).not.toBeInTheDocument();
});

it("loads the usage again when the processing status changes", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ stages: [], total: null } as never);
  const view = render(<InvoiceModelUsage documentId="doc-1" processingStatus="running" />);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
  view.rerender(<InvoiceModelUsage documentId="doc-1" processingStatus="failed" />);
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
});
