import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { InvoiceDocumentsPage } from "./InvoiceDocumentsPage";
import { listInvoiceDocuments, uploadInvoiceDocument } from "../api/endpoints";
import { ApiError } from "../api/client";

vi.mock("../api/endpoints", () => ({
  listInvoiceDocuments: vi.fn().mockResolvedValue([]),
  uploadInvoiceDocument: vi.fn().mockResolvedValue(undefined),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listInvoiceDocuments).mockResolvedValue([]);
});

it("provides a named receipt file input and uploads the selected PDF", async () => {
  render(<MemoryRouter><InvoiceDocumentsPage /></MemoryRouter>);
  await screen.findByText("Keine Belege vorhanden.");
  const input = screen.getByLabelText("Belegdatei", { exact: true });
  expect(input).toHaveAttribute("type", "file");
  expect(input).toHaveAccessibleName("Belegdatei");
  expect(input).toHaveAttribute("accept", "application/pdf,application/xml,text/xml");
  const file = new File(["%PDF-1.4\n"], "synthetic-receipt.pdf", { type: "application/pdf" });
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: /^Hochladen$/ }));
  await waitFor(() => expect(uploadInvoiceDocument).toHaveBeenCalledWith(file));
  await waitFor(() => expect(listInvoiceDocuments).toHaveBeenCalledTimes(2));
});

it("preserves an upload error instead of reporting success", async () => {
  vi.mocked(uploadInvoiceDocument).mockRejectedValueOnce(new ApiError(413, "Datei zu groß"));
  render(<MemoryRouter><InvoiceDocumentsPage /></MemoryRouter>);
  await screen.findByText("Keine Belege vorhanden.");
  const file = new File(["%PDF-1.4\n"], "synthetic-receipt.pdf", { type: "application/pdf" });
  fireEvent.change(screen.getByLabelText("Belegdatei", { exact: true }), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: /^Hochladen$/ }));
  expect(await screen.findByText("Datei zu groß")).toBeVisible();
  expect(listInvoiceDocuments).toHaveBeenCalledTimes(1);
});
