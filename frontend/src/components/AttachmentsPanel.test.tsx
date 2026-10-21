import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { AttachmentsPanel } from "./AttachmentsPanel";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import type { Attachment, Property } from "../api/types";

vi.mock("../api/endpoints", async (original) => ({
  ...await original<typeof endpoints>(),
  listAttachments: vi.fn(), uploadAttachment: vi.fn(), archiveAttachment: vi.fn(), listProperties: vi.fn(),
}));
const document: Attachment = {
  id: "doc/one", property_id: "property", unit_id: "unit", person_id: null, lease_agreement_id: null,
  title: "Vertrag", original_filename: "<script>vertrag.pdf", mime_type: "application/pdf",
  content_byte_size: 12, content_sha256: "a".repeat(64), uploaded_by: "user", created_at: "2026-10-21T00:00:00Z",
  archived_at: null, archived_by: null, archive_reason: null,
};
const file = new File(["%PDF-1"], "vertrag.pdf", { type: "application/pdf" });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((r) => { resolve = r; }); return { promise, resolve }; }
async function draft() {
  await userEvent.upload(screen.getByLabelText("Dokumentdatei"), file);
  fireEvent.change(screen.getByLabelText("Dokumenttitel"), { target: { value: "Mein Vertrag" } });
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(endpoints.listAttachments).mockResolvedValue({ items: [], next_offset: null });
  vi.mocked(endpoints.listProperties).mockResolvedValue([{ id: "p1", name: "Haus am See", street: "Seestr. 1", zip_code: "12345", city: "Berlin" }, { id: "p2", name: "Markthaus", street: "Markt 2", city: "Bonn" }] as Property[]);
  vi.mocked(endpoints.uploadAttachment).mockResolvedValue(document);
  vi.mocked(endpoints.archiveAttachment).mockResolvedValue({ ...document, archived_at: "2026-10-21T01:00:00Z", archive_reason: "Erledigt" });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("loads, shows empty state, uploads the unit PDF and refreshes only after success", async () => {
  const pending = deferred<Attachment>();
  vi.mocked(endpoints.uploadAttachment).mockReturnValue(pending.promise);
  render(<AttachmentsPanel propertyId="property" unitId="unit" canWrite />);
  await screen.findByText("Keine Dokumente vorhanden.");
  expect(endpoints.listAttachments).toHaveBeenCalledWith(expect.objectContaining({ property_id: "property", unit_id: "unit", include_archived: false, offset: 0, limit: 50 }));
  await draft();
  const submit = screen.getByRole("button", { name: "Dokument hochladen" });
  fireEvent.submit(submit.closest("form")!); fireEvent.submit(submit.closest("form")!);
  expect(endpoints.uploadAttachment).toHaveBeenCalledTimes(1);
  expect(endpoints.uploadAttachment).toHaveBeenCalledWith(file, { property_id: "property", unit_id: "unit", title: "Mein Vertrag" });
  expect(screen.queryByText("Dokument hochgeladen.")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Dokumentdatei")).toBeDisabled();
  await act(async () => pending.resolve(document));
  await screen.findByText("Dokument hochgeladen.");
  expect(screen.getByLabelText("Dokumenttitel")).toHaveValue("");
  expect(endpoints.listAttachments).toHaveBeenCalledTimes(2);
});
it.each([403, 404, 409, 413, 422, "network"])("retains the file and title on upload failure %s without success", async (status) => {
  vi.mocked(endpoints.uploadAttachment).mockRejectedValue(status === "network" ? new TypeError("Failed to fetch") : new ApiError(status as number, "Rejected"));
  render(<AttachmentsPanel propertyId="property" canWrite />); await screen.findByText("Keine Dokumente vorhanden."); await draft();
  fireEvent.click(screen.getByRole("button", { name: "Dokument hochladen" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText("Dokumenttitel")).toHaveValue("Mein Vertrag");
  expect((screen.getByLabelText("Dokumentdatei") as HTMLInputElement).files?.[0]).toBe(file);
  expect(screen.queryByText("Dokument hochgeladen.")).not.toBeInTheDocument();
  expect(endpoints.listAttachments).toHaveBeenCalledTimes(1);
});
it("rejects missing, empty and over-25-MiB files locally, then permits a changed valid file", async () => {
  render(<AttachmentsPanel propertyId="property" canWrite />); await screen.findByText("Keine Dokumente vorhanden.");
  fireEvent.submit(screen.getByRole("button", { name: "Dokument hochladen" }).closest("form")!);
  expect(endpoints.uploadAttachment).not.toHaveBeenCalled();
  for (const invalid of [new File([], "empty.pdf"), Object.defineProperty(new File(["x"], "big.pdf"), "size", { value: 25 * 1024 * 1024 + 1 })]) {
    await userEvent.upload(screen.getByLabelText("Dokumentdatei"), invalid);
    fireEvent.submit(screen.getByRole("button", { name: "Dokument hochladen" }).closest("form")!);
    expect(endpoints.uploadAttachment).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toBeVisible();
  }
  await draft(); fireEvent.click(screen.getByRole("button", { name: "Dokument hochladen" })); await screen.findByText("Dokument hochgeladen.");
});
it("opens a credentialed same-origin API URL, not fetched HTML, and escapes original filenames", async () => {
  vi.mocked(endpoints.listAttachments).mockResolvedValue({ items: [document, { ...document, id: "binary", title: "Binär", mime_type: "text/html" }], next_offset: null });
  const open = vi.spyOn(window, "open").mockReturnValue(null);
  render(<AttachmentsPanel propertyId="property" canWrite={false} />);
  await screen.findAllByRole("button", { name: "Öffnen" });
  expect(screen.getAllByText(/<script>vertrag.pdf/)[0]).toBeVisible();
  fireEvent.click(screen.getAllByRole("button", { name: "Öffnen" })[0]);
  expect(open).toHaveBeenLastCalledWith("/api/attachments/doc%2Fone/download?inline=true", "_blank", "noopener,noreferrer");
  fireEvent.click(screen.getAllByRole("button", { name: "Öffnen" })[1]);
  expect(open).toHaveBeenLastCalledWith("/api/attachments/binary/download", "_blank", "noopener,noreferrer");
  expect(screen.queryByLabelText("Dokumentdatei")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Archivieren" })).not.toBeInTheDocument();
});
it("archives with a required reason, synchronous lock and blocked pending dismissal, preserving originals", async () => {
  vi.mocked(endpoints.listAttachments).mockResolvedValue({ items: [document], next_offset: null });
  const pending = deferred<Attachment>(); vi.mocked(endpoints.archiveAttachment).mockReturnValue(pending.promise);
  render(<AttachmentsPanel propertyId="property" canWrite />); fireEvent.click(await screen.findByRole("button", { name: "Archivieren" }));
  const dialog = screen.getByRole("dialog");
  fireEvent.submit(within(dialog).getByRole("button", { name: "Dokument archivieren" }).closest("form")!);
  expect(endpoints.archiveAttachment).not.toHaveBeenCalled();
  fireEvent.change(within(dialog).getByLabelText("Archivierungsgrund"), { target: { value: " Erledigt " } });
  fireEvent.submit(within(dialog).getByRole("button", { name: "Dokument archivieren" }).closest("form")!);
  fireEvent.submit(within(dialog).getByRole("button", { name: "Dokument archivieren" }).closest("form")!);
  expect(endpoints.archiveAttachment).toHaveBeenCalledExactlyOnceWith("doc/one", "Erledigt");
  expect(within(dialog).getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  fireEvent.keyDown(dialog, { key: "Escape" }); fireEvent.click(within(dialog).getByRole("button", { name: "Schließen" })); expect(screen.getByRole("dialog")).toBeVisible();
  await act(async () => pending.resolve({ ...document, archived_at: "now" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  fireEvent.click(screen.getByLabelText("Archivierte anzeigen"));
  await waitFor(() => expect(endpoints.listAttachments).toHaveBeenLastCalledWith(expect.objectContaining({ include_archived: true, offset: 0 })));
  expect(screen.queryByRole("button", { name: /Löschen|Ersetzen|Wiederherstellen/ })).not.toBeInTheDocument();
});
it.each([403, 404, 409, 413, 422, "network"])("keeps archive reason and dialog on failure %s and allows cancellation", async (status) => {
  vi.mocked(endpoints.listAttachments).mockResolvedValue({ items: [document], next_offset: null });
  vi.mocked(endpoints.archiveAttachment).mockRejectedValue(status === "network" ? new TypeError("offline") : new ApiError(status as number, "rejected"));
  render(<AttachmentsPanel propertyId="property" canWrite />); fireEvent.click(await screen.findByRole("button", { name: "Archivieren" }));
  fireEvent.change(screen.getByLabelText("Archivierungsgrund"), { target: { value: "Behalten" } }); fireEvent.click(screen.getByRole("button", { name: "Dokument archivieren" }));
  await screen.findByRole("alert"); expect(screen.getByLabelText("Archivierungsgrund")).toHaveValue("Behalten");
  expect(screen.queryByText("Dokument archiviert.")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Abbrechen" })); expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
it("lists person documents across properties but requires a real human-readable upload property selection", async () => {
  render(<AttachmentsPanel personId="person" canWrite />); await screen.findByText("Keine Dokumente vorhanden.");
  await screen.findByRole("option", { name: /Haus am See/ });
  expect(endpoints.listAttachments).toHaveBeenCalledWith(expect.objectContaining({ person_id: "person" }));
  expect(vi.mocked(endpoints.listAttachments).mock.calls[0][0]).not.toHaveProperty("property_id");
  await draft(); fireEvent.submit(screen.getByRole("button", { name: "Dokument hochladen" }).closest("form")!); expect(endpoints.uploadAttachment).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Objekt *"), { target: { value: "p2" } });
  fireEvent.click(screen.getByRole("button", { name: "Dokument hochladen" })); await screen.findByText("Dokument hochgeladen.");
  expect(endpoints.uploadAttachment).toHaveBeenCalledWith(file, { property_id: "p2", person_id: "person", title: "Mein Vertrag" });
  expect(endpoints.listAttachments).toHaveBeenLastCalledWith(expect.not.objectContaining({ property_id: expect.anything() }));
});
it("retries failed list/property requests and paginates without losing earlier documents", async () => {
  vi.mocked(endpoints.listAttachments).mockRejectedValueOnce(new ApiError(403, "denied")).mockResolvedValueOnce({ items: [document], next_offset: 50 }).mockResolvedValue({ items: [{ ...document, id: "next", title: "Weiterer Vertrag" }], next_offset: null });
  render(<AttachmentsPanel propertyId="property" canWrite={false} />); await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Erneut versuchen" })); await screen.findByText("Vertrag");
  fireEvent.click(screen.getByRole("button", { name: "Weitere Dokumente laden" })); await screen.findByText("Weiterer Vertrag"); expect(screen.getByText("Vertrag")).toBeVisible();
  expect(endpoints.listAttachments).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50 }));
});
it("ignores late lists after archive-filter changes", async () => {
  const old = deferred<{ items: Attachment[]; next_offset: null }>(); vi.mocked(endpoints.listAttachments).mockReturnValueOnce(old.promise);
  render(<AttachmentsPanel propertyId="property" canWrite={false} />);
  fireEvent.click(screen.getByLabelText("Archivierte anzeigen")); await screen.findByText("Keine Dokumente vorhanden.");
  await act(async () => old.resolve({ items: [document], next_offset: null })); expect(screen.queryByText("Vertrag")).not.toBeInTheDocument();
});
it("discards late mutations and drafts on parent changes and unmount", async () => {
  const pending = deferred<Attachment>(); vi.mocked(endpoints.uploadAttachment).mockReturnValue(pending.promise);
  const view = render(<AttachmentsPanel propertyId="old" canWrite />); await screen.findByText("Keine Dokumente vorhanden."); await draft(); fireEvent.click(screen.getByRole("button", { name: "Dokument hochladen" }));
  view.rerender(<AttachmentsPanel propertyId="new" canWrite />); await screen.findByText("Keine Dokumente vorhanden."); expect(screen.getByLabelText("Dokumenttitel")).toHaveValue("");
  await act(async () => pending.resolve(document)); expect(screen.queryByText("Dokument hochgeladen.")).not.toBeInTheDocument();
  expect(endpoints.listAttachments).toHaveBeenCalledTimes(2); view.unmount();
});
it("retries property choices independently without narrowing the person list", async () => {
  vi.mocked(endpoints.listProperties).mockRejectedValueOnce(new TypeError("offline"));
  render(<AttachmentsPanel personId="person" canWrite />); await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "Objekte erneut laden" })); await screen.findByRole("option", { name: /Haus am See/ });
  expect(endpoints.listAttachments).toHaveBeenCalledTimes(1);
});
it("keeps archived originals readable and never offers another archive mutation", async () => {
  vi.mocked(endpoints.listAttachments).mockResolvedValue({ items: [{ ...document, archived_at: "2026-10-21T00:00:00Z", archive_reason: "Altvertrag" }], next_offset: null });
  render(<AttachmentsPanel propertyId="property" canWrite />);
  expect(await screen.findByText(/Archiviert: Altvertrag/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Öffnen" })).toBeEnabled();
  expect(screen.queryByRole("button", { name: "Archivieren" })).not.toBeInTheDocument();
});
it("does not let a late person property choice response leak into the next contact", async () => {
  const old = deferred<Property[]>(); vi.mocked(endpoints.listProperties).mockReturnValueOnce(old.promise).mockResolvedValue([{ id: "new-property", name: "Neues Haus" }] as Property[]);
  const view = render(<AttachmentsPanel personId="old-person" canWrite />);
  expect(screen.getByText("Lade Objekte…")).toBeVisible();
  view.rerender(<AttachmentsPanel personId="new-person" canWrite />);
  await screen.findByRole("option", { name: "Neues Haus" });
  await act(async () => old.resolve([{ id: "old-property", name: "Altes Haus" }] as Property[]));
  expect(screen.queryByRole("option", { name: "Altes Haus" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Objekt *")).toHaveValue("");
  expect(endpoints.listAttachments).toHaveBeenLastCalledWith(expect.objectContaining({ person_id: "new-person" }));
});
it("ignores a late list when its parent changes", async () => {
  const old = deferred<{ items: Attachment[]; next_offset: null }>(); vi.mocked(endpoints.listAttachments).mockReturnValueOnce(old.promise);
  const view = render(<AttachmentsPanel propertyId="old-property" unitId="old-unit" canWrite={false} />);
  view.rerender(<AttachmentsPanel propertyId="new-property" unitId="new-unit" canWrite={false} />);
  await screen.findByText("Keine Dokumente vorhanden.");
  await act(async () => old.resolve({ items: [document], next_offset: null }));
  expect(screen.queryByText("Vertrag")).not.toBeInTheDocument();
  expect(endpoints.listAttachments).toHaveBeenLastCalledWith(expect.objectContaining({ property_id: "new-property", unit_id: "new-unit" }));
});
it("does not refresh or announce an upload after unmount", async () => {
  const pending = deferred<Attachment>(); vi.mocked(endpoints.uploadAttachment).mockReturnValue(pending.promise);
  const view = render(<AttachmentsPanel propertyId="property" canWrite />); await screen.findByText("Keine Dokumente vorhanden.");
  await draft(); fireEvent.click(screen.getByRole("button", { name: "Dokument hochladen" }));
  expect(endpoints.uploadAttachment).toHaveBeenCalledTimes(1); view.unmount();
  await act(async () => pending.resolve(document)); expect(endpoints.listAttachments).toHaveBeenCalledTimes(1);
});
it("blocks changing the upload file, title and property during a pending mutation", async () => {
  const pending = deferred<Attachment>(); vi.mocked(endpoints.uploadAttachment).mockReturnValue(pending.promise);
  render(<AttachmentsPanel personId="person" canWrite />); await screen.findByRole("option", { name: /Haus am See/ });
  fireEvent.change(screen.getByLabelText("Objekt *"), { target: { value: "p1" } }); await draft();
  fireEvent.click(screen.getByRole("button", { name: "Dokument hochladen" }));
  expect(screen.getByLabelText("Objekt *")).toBeDisabled(); expect(screen.getByLabelText("Dokumentdatei")).toBeDisabled(); expect(screen.getByLabelText("Dokumenttitel")).toBeDisabled();
  expect(screen.getByLabelText("Archivierte anzeigen")).toBeDisabled();
  await act(async () => pending.resolve(document)); await screen.findByText("Dokument hochgeladen.");
  expect(endpoints.uploadAttachment).toHaveBeenCalledExactlyOnceWith(file, { property_id: "p1", person_id: "person", title: "Mein Vertrag" });
});
it("clears old file errors and success when another file is selected", async () => {
  vi.mocked(endpoints.uploadAttachment).mockRejectedValueOnce(new ApiError(422, "bad file"));
  render(<AttachmentsPanel propertyId="property" canWrite />); await screen.findByText("Keine Dokumente vorhanden.");
  await draft(); fireEvent.click(screen.getByRole("button", { name: "Dokument hochladen" })); await screen.findByRole("alert");
  const replacement = new File(["new"], "new.pdf"); await userEvent.upload(screen.getByLabelText("Dokumentdatei"), replacement);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument(); expect(screen.getByLabelText("Dokumenttitel")).toHaveValue("Mein Vertrag");
  fireEvent.click(screen.getByRole("button", { name: "Dokument hochladen" })); await screen.findByText("Dokument hochgeladen.");
  await userEvent.upload(screen.getByLabelText("Dokumentdatei"), file); expect(screen.queryByText("Dokument hochgeladen.")).not.toBeInTheDocument();
});
