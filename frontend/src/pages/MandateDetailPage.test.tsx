import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { MandateDetailPage } from "./MandateDetailPage";
import { api } from "../api/client";

vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role: "CLERK" } }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const mandate = { id: "mandate", label: "Source mandate", mandate_type: "WEG", start_date: "2025-01-01", end_date: null };
const doc = { id: "doc", management_mandate_id: "mandate", accounting_scope_id: "scope", scope_reason: "Reviewed", archived_at: null, archive_reason: null,
  doc_type: "VERWALTERVERTRAG", title: "Source contract", original_filename: "contract.pdf", content_byte_size: 100, uploaded_at: "2025-01-01", uploaded_by: "actor" };
const renderPage = () => render(<MemoryRouter initialEntries={["/mandate/mandate"]}><Routes><Route path="/mandate/:mandateId" element={<MandateDetailPage />} /></Routes></MemoryRouter>);

it("describes visible documents as partial and preserves original evidence through explicit reasoned archive", async () => {
  let archived = false;
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url.includes("/documents")) return archived && !url.includes("true") ? [] : [{ ...doc, archived_at: archived ? "2026-10-02" : null, archive_reason: archived ? "Replaced" : null }];
    if (url.includes("/accounting-scopes")) return { items: [], next_cursor: null };
    return mandate;
  });
  const post = vi.spyOn(api, "post").mockImplementation(async () => { archived = true; return { ...doc, archived_at: "2026-10-02" }; });
  renderPage();
  await screen.findByText("Source contract");
  expect(screen.getByText(/keine vollständige Mandatsakte/)).toHaveTextContent("bleiben in Quarantäne und sind hier nicht zugänglich");
  expect(screen.queryByText(/bisherige Zugriff innerhalb der Organisation/)).toBeNull();
  expect(screen.queryByRole("button", { name: "Löschen" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Archivieren" }));
  expect(screen.getByRole("button", { name: "Archivierung speichern" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Begründung der Archivierung"), { target: { value: "Replaced" } });
  fireEvent.click(screen.getByRole("button", { name: "Archivierung speichern" }));
  await screen.findByText("Keine zugänglichen Unterlagen für diese Auswahl.");
  expect(post).toHaveBeenCalledWith("/api/mandates/mandate/documents/doc/archive", { reason: "Replaced" });
  fireEvent.click(screen.getByLabelText("Archivierte Unterlagen anzeigen"));
  await screen.findByText("Source contract");
  expect(screen.queryByRole("button", { name: "Archivieren" })).toBeNull();
  expect(screen.getByRole("link", { name: "Öffnen" })).toHaveAttribute("href", "/api/mandates/mandate/documents/doc/download");
});

it("hides stale documents after a failed reload instead of claiming an empty mandate", async () => {
  let failing = false;
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url.includes("/documents")) { if (failing) throw new Error("Access changed"); return [doc]; }
    if (url.includes("/accounting-scopes")) return { items: [], next_cursor: null };
    return mandate;
  });
  renderPage();
  await screen.findByText("Source contract");
  failing = true;
  fireEvent.click(screen.getByRole("button", { name: "Unterlagen neu laden" }));
  await screen.findByRole("alert");
  expect(screen.queryByText("Source contract")).toBeNull();
  expect(screen.queryByText("Keine zugänglichen Unterlagen für diese Auswahl.")).toBeNull();
});

it("requires an explicit scope and review reason before uploading an independently held original", async () => {
  vi.spyOn(api, "get").mockImplementation(async url => {
    if (url.includes("/documents")) return [];
    if (url.includes("/accounting-scopes")) return { items: [], next_cursor: null };
    return mandate;
  });
  renderPage();
  const file = await screen.findByLabelText("Datei *");
  fireEvent.change(file, { target: { files: [new File(["synthetic original"], "original.pdf", { type: "application/pdf" })] } });
  expect(screen.getByRole("button", { name: "Hochladen" })).toBeDisabled();
  expect(screen.getByText(/eigenständig vorliegendes Original/)).toBeVisible();
});
