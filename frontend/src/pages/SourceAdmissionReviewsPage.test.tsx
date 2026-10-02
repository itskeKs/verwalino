import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SourceAdmissionReviewsPage } from "./SourceAdmissionReviewsPage";
import { api, ApiError } from "../api/client";

const source = { id: "original", document_id: "original", import_id: "import", property_id: "property", property_name: "Haus am Park",
  title: "Teilungserklärung", original_filename: "declaration.pdf", draft_revision: 7, content_sha256: "a".repeat(64) };
let role = "OWNER_ADMIN";
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: { role } }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); role = "OWNER_ADMIN"; });
function show(path = "/quellenfreigaben") {
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/quellenfreigaben" element={<SourceAdmissionReviewsPage />} /><Route path="/quellenfreigaben/:reviewId" element={<SourceAdmissionReviewsPage />} /></Routes></MemoryRouter>);
}

it("requires explicit source review and retains the exact command after a lost response", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [source], next_cursor: null });
  const post = vi.spyOn(api, "post").mockRejectedValueOnce(new Error("Lost response")).mockResolvedValue({ ...source, id: "review", domain_id: "domain", reviewed_by: "actor", reason: "Original und Ziel geprüft", created_at: "2026-10-02T12:00:00Z" });
  show();
  expect(await screen.findByRole("button", { name: "Quelle freigeben" })).toBeDisabled();
  expect(screen.getByText(/Zugriffskontrolle ist noch nicht aktiviert/)).toBeVisible();
  expect(screen.getByRole("link", { name: /Original prüfen/ })).toHaveAttribute("href", "/api/property-source-admission-reviews/originals/original/content");
  fireEvent.change(screen.getByLabelText("Begründung der Quellenfreigabe"), { target: { value: "Original und Ziel geprüft" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Quelle freigeben" }));
  await screen.findByText(/Keine bestätigte Antwort erhalten/);
  expect(screen.getByLabelText("Begründung der Quellenfreigabe")).toHaveValue("Original und Ziel geprüft");
  expect(screen.getByLabelText("Begründung der Quellenfreigabe")).toBeDisabled();
  expect(screen.getByRole("checkbox")).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Denselben Auftrag wiederholen" }));
  await screen.findByRole("link", { name: "Freigabenachweis öffnen" });
  expect(post.mock.calls[0]).toEqual(post.mock.calls[1]);
  expect(post.mock.calls[0][1]).toEqual({ document_id: "original", import_id: "import", draft_revision: 7, content_sha256: "a".repeat(64), reason: "Original und Ziel geprüft" });
});

it("shows current denial without a success receipt", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [source], next_cursor: null });
  vi.spyOn(api, "post").mockRejectedValue(new ApiError(404, "Unavailable"));
  show();
  await screen.findByRole("checkbox");
  fireEvent.change(screen.getByLabelText("Begründung der Quellenfreigabe"), { target: { value: "Geprüft" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Quelle freigeben" }));
  await screen.findByText("Die Quelle ist mit Ihren aktuellen Rechten nicht mehr verfügbar.");
  expect(screen.queryByRole("region", { name: "Freigabenachweis" })).not.toBeInTheDocument();
});

it("loads a stable receipt and hides it when current access is denied", async () => {
  vi.spyOn(api, "get").mockRejectedValue(new ApiError(404, "Unavailable"));
  show("/quellenfreigaben/review");
  await screen.findByText("Freigabenachweis mit Ihren aktuellen Rechten nicht verfügbar.");
  expect(screen.queryByRole("button", { name: "Quelle freigeben" })).not.toBeInTheDocument();
});

it("does not request pending sources for read-only members", () => {
  role = "VIEWER";
  const get = vi.spyOn(api, "get");
  show();
  expect(screen.getByText("Quellenfreigaben erfordern Schreibrechte.")).toBeVisible();
  expect(get).not.toHaveBeenCalled();
});

it("reloads a changed source after a definitive conflict and submits a newly confirmed command", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ items: [source], next_cursor: null });
  const post = vi.spyOn(api, "post").mockRejectedValue(new ApiError(409, "Stale revision"));
  show();
  await screen.findByRole("checkbox");
  fireEvent.change(screen.getByLabelText("Begründung der Quellenfreigabe"), { target: { value: "Erste Prüfung" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Quelle freigeben" }));
  await screen.findByText(/Der Prüfstand hat sich geändert/);
  get.mockResolvedValue({ items: [{ ...source, draft_revision: 8 }], next_cursor: null });
  fireEvent.click(screen.getByRole("button", { name: "Liste neu laden" }));
  await screen.findByText(/Revision 8/);
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(screen.getByLabelText("Begründung der Quellenfreigabe")).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Begründung der Quellenfreigabe"), { target: { value: "Neue Revision geprüft" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "Quelle freigeben" }));
  await screen.findByText(/Der Prüfstand hat sich geändert/);
  expect(post.mock.calls[1][1]).toMatchObject({ draft_revision: 8, reason: "Neue Revision geprüft" });
  expect(post.mock.calls[1][2]?.headers).not.toEqual(post.mock.calls[0][2]?.headers);
});
