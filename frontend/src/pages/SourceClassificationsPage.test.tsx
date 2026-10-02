import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SourceClassificationsPage } from "./SourceClassificationsPage";
import { api, ApiError } from "../api/client";

const source = { id: "original", document_id: "original", property_id: "property", property_name: "Haus am Park",
  content_sha256: "a".repeat(64), source_context_digest: "b".repeat(64), source_uploaded_by: "actor", linked_import_count: 1,
  can_admit: true, can_preview: true, quiescent: true };
let role = "OWNER_ADMIN";
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role } }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); role = "OWNER_ADMIN"; });
function show(path = "/altquellen") {
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/altquellen" element={<SourceClassificationsPage />} /><Route path="/altquellen/:reviewId" element={<SourceClassificationsPage />} /></Routes></MemoryRouter>);
}
function confirm() {
  fireEvent.change(screen.getByLabelText("Begründung der Einstufung"), { target: { value: "Original und Ziel geprüft" } });
  fireEvent.click(screen.getByRole("checkbox"));
}

it("retains exact classification, digest and key after an unconfirmed response", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [source], next_cursor: null });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Lost reply")).mockResolvedValue({ ...source, id: "receipt", decision: "ADMITTED", reason: "Original und Ziel geprüft", created_at: "2026-10-02T12:00:00Z" });
  show();
  expect(await screen.findByRole("button", { name: "Einstufung speichern" })).toBeDisabled();
  expect(screen.getByText(/schränkt den bisherigen Zugriff noch nicht ein/)).toBeVisible();
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Einstufung speichern" }));
  await screen.findByText(/Keine bestätigte Antwort erhalten/);
  expect(screen.getByRole("combobox")).toBeDisabled();
  expect(screen.getByLabelText("Begründung der Einstufung")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Denselben Auftrag wiederholen" }));
  await screen.findByRole("link", { name: "Prüfnachweis öffnen" });
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  expect(post.mock.calls[0][1]).toEqual({ document_id: "original", property_id: "property", content_sha256: source.content_sha256,
    source_context_digest: source.source_context_digest, decision: "ADMITTED", reason: "Original und Ziel geprüft" });
});

it("offers only quarantine and no original link for unknown custody", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [{ ...source, can_admit: false, can_preview: false, source_uploaded_by: null }], next_cursor: null });
  show(); await screen.findByRole("combobox");
  expect(screen.getByRole("combobox")).toHaveValue("QUARANTINED");
  expect(screen.getByRole("option", { name: "Quelle freigeben" })).toBeDisabled();
  expect(screen.queryByRole("link", { name: "Eigenes Original prüfen" })).not.toBeInTheDocument();
  expect(screen.getByText(/Herkunft umzuschreiben/)).toBeVisible();
});

it("requires renewed confirmation after changing the decision", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [source], next_cursor: null });
  show(); await screen.findByRole("checkbox"); confirm();
  expect(screen.getByRole("button", { name: "Einstufung speichern" })).toBeEnabled();
  fireEvent.change(screen.getByRole("combobox"), { target: { value: "QUARANTINED" } });
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Einstufung speichern" })).toBeDisabled();
});

it("requires refreshed context and new confirmation after conflict", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ items: [source], next_cursor: null });
  const post = vi.spyOn(api, "post").mockRejectedValue(new ApiError(409, "Stale context"));
  show(); await screen.findByRole("checkbox"); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Einstufung speichern" }));
  await screen.findByText(/Prüfstand hat sich geändert/);
  get.mockResolvedValue({ items: [{ ...source, source_context_digest: "c".repeat(64) }], next_cursor: null });
  fireEvent.click(screen.getByRole("button", { name: "Liste neu laden" }));
  await screen.findByRole("button", { name: "Einstufung speichern" });
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Einstufung speichern" }));
  await screen.findByText(/Prüfstand hat sich geändert/);
  expect(post.mock.calls[1][1]).toMatchObject({ source_context_digest: "c".repeat(64) });
  expect(post.mock.calls[0][2]).not.toEqual(post.mock.calls[1][2]);
});

it("does not request pending sources for a clerk", () => {
  role = "CLERK"; const get = vi.spyOn(api, "get"); show();
  expect(screen.getByText(/Einstufung erfordert Administratorrechte/)).toBeVisible();
  expect(get).not.toHaveBeenCalled();
});

it("does not expose a receipt after current access is denied", async () => {
  vi.spyOn(api, "get").mockRejectedValue(new ApiError(404, "Denied"));
  show("/altquellen/receipt"); await screen.findByText(/Prüfnachweis mit Ihren aktuellen Rechten nicht verfügbar/);
  expect(screen.queryByRole("region", { name: "Prüfnachweis" })).not.toBeInTheDocument();
});
