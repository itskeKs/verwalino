import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MasterImport } from "./MasterImport";
import { PropertiesPage } from "../pages/PropertiesPage";
import * as imports from "../api/masterImport";
import { api, ApiError } from "../api/client";

const auth = vi.hoisted(() => ({ role: "OWNER_ADMIN", user_id: "actor" }));
vi.mock("../auth/useAuth", () => ({ useAuth: () => ({ session: auth }) }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); auth.role = "OWNER_ADMIN"; });
const csv = "kind;key;property_key\nproperty;house;\nunit;flat;house\nperson;contact;";
const preview: imports.MasterImportPreview = {
  rows: [
    { line: 2, kind: "property", key: "house", values: { name: "Testhaus" } },
    { line: 3, kind: "unit", key: "flat", values: { property_key: "house", number: "1", area: "75,25" } },
    { line: 4, kind: "person", key: "contact", values: { first_name: "Ada", last_name: "Test", email: "ada@example.test" } },
  ], errors: [], counts: { properties: 1, units: 1, persons: 1 }, source_digest: "digest-1",
};
const result: imports.MasterImportResult = { rows: [
  { line: 2, kind: "property", key: "house", id: "property-id" },
  { line: 3, kind: "unit", key: "flat", id: "unit-id" },
  { line: 4, kind: "person", key: "contact", id: "person-id" },
], counts: preview.counts };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
function show(onImported = vi.fn()) { render(<MemoryRouter><MasterImport onImported={onImported} /></MemoryRouter>); return onImported; }
async function selectFile(text = csv, name = "stammdaten.csv") {
  fireEvent.change(screen.getByLabelText("CSV-Datei"), { target: { files: [new File([text], name, { type: "text/csv" })] } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Vorschau prüfen" })).toBeEnabled());
}
async function cleanPreview() {
  await selectFile(); fireEvent.click(screen.getByRole("button", { name: "Vorschau prüfen" }));
  await screen.findByRole("table", { name: "CSV-Vorschau" });
}
function confirm() { fireEvent.click(screen.getByRole("checkbox", { name: /geprüften CSV-Zeilen/ })); }
it("keeps the entry closed without import requests and opens a real template link", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ items: [], next_cursor: null });
  const post = vi.spyOn(api, "post");
  render(<MemoryRouter><PropertiesPage /></MemoryRouter>);
  await screen.findByText("Keine Objekte vorhanden.");
  expect(screen.queryByLabelText("CSV-Datei")).not.toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(1); expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "CSV-Stammdaten importieren" }));
  expect(screen.getByRole("link", { name: "CSV-Vorlage herunterladen" })).toHaveAttribute("href", "/api/properties/import/template.csv");
  expect(screen.getByRole("link", { name: "CSV-Vorlage herunterladen" })).toHaveAttribute("download");
  expect(screen.getByLabelText("CSV-Datei")).toBeVisible(); expect(get).toHaveBeenCalledTimes(1);
});
it.each(["READ_ONLY", "UNKNOWN"])("hides all import controls for %s", async role => {
  auth.role = role; vi.spyOn(api, "get").mockResolvedValue({ items: [], next_cursor: null });
  render(<MemoryRouter><PropertiesPage /><MasterImport onImported={vi.fn()} /></MemoryRouter>);
  await screen.findByText("Keine Objekte vorhanden.");
  expect(screen.queryByRole("button", { name: "CSV-Stammdaten importieren" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("CSV-Datei")).not.toBeInTheDocument();
});
it("previews all three kinds, preserves decimal text and requires explicit confirmation", async () => {
  const post = vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview);
  const apply = vi.spyOn(imports, "applyMasterImport").mockResolvedValue(result);
  const reload = show(); await cleanPreview();
  expect(post).toHaveBeenCalledWith({ csv_text: csv, data_audience: null }, expect.any(AbortSignal));
  const table = screen.getByRole("table", { name: "CSV-Vorschau" });
  for (const text of ["property", "unit", "person", "75,25", "ada@example.test"]) expect(within(table).getByText(text)).toBeVisible();
  expect(screen.getByText("1 Objekte · 1 Einheiten · 1 Kontakte")).toBeVisible();
  expect(screen.getByRole("button", { name: "Stammdaten importieren" })).toBeDisabled();
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Stammdaten importieren" }));
  await screen.findByRole("region", { name: "Importergebnis" });
  expect(apply).toHaveBeenCalledTimes(1); expect(reload).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("link", { name: "property house: property-id" })).toHaveAttribute("href", "/objekte/property-id");
  expect(screen.getByRole("link", { name: "unit flat: unit-id" })).toHaveAttribute("href", "/objekte/property-id/einheiten/unit-id");
  expect(screen.getByRole("link", { name: "person contact: person-id" })).toHaveAttribute("href", "/personen/person-id");
  expect(screen.queryByRole("button", { name: "Stammdaten importieren" })).not.toBeInTheDocument();
});
it("shows successful preview row errors and blocks apply", async () => {
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue({ ...preview, errors: [{ line: 3, field: "area", message: "Ungültige Fläche" }] });
  const apply = vi.spyOn(imports, "applyMasterImport"); show(); await cleanPreview();
  const errors = screen.getByRole("region", { name: "Zeilenfehler" });
  expect(errors).toHaveTextContent("Zeile 3 · area: Ungültige Fläche");
  expect(screen.queryByRole("checkbox", { name: /geprüften CSV-Zeilen/ })).not.toBeInTheDocument();
  expect(apply).not.toHaveBeenCalled();
});
it.each([401, 403])("shows a %s API error and permits explicit preview retry", async status => {
  const post = vi.spyOn(imports, "previewMasterImport").mockRejectedValueOnce(new ApiError(status, "Keine Berechtigung")).mockResolvedValue(preview);
  show(); await selectFile(); fireEvent.click(screen.getByRole("button", { name: "Vorschau prüfen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Keine Berechtigung");
  fireEvent.click(screen.getByRole("button", { name: "Vorschau prüfen" }));
  await screen.findByRole("table", { name: "CSV-Vorschau" }); expect(post).toHaveBeenCalledTimes(2);
});
it.each([new Error("Antwort verloren"), new ApiError(503, "Unbekannter Ausgang")])("retains the exact apply body/UUID for an uncertain retry: %s", async failure => {
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview);
  const apply = vi.spyOn(imports, "applyMasterImport").mockRejectedValueOnce(failure).mockResolvedValue(result);
  const reload = show(); await cleanPreview(); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Stammdaten importieren" }));
  await screen.findByRole("alert"); expect(reload).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Import erneut versuchen" }));
  await screen.findByRole("region", { name: "Importergebnis" });
  expect(apply).toHaveBeenCalledTimes(2); expect(apply.mock.calls[0][0]).toEqual(apply.mock.calls[1][0]);
  expect(apply.mock.calls[0][0]).toMatchObject({ csv_text: csv, data_audience: null, expected_digest: "digest-1", request_id: expect.stringMatching(/^[0-9a-f-]{36}$/) });
  expect(reload).toHaveBeenCalledTimes(1);
});
it("aborts and ignores a late preview after a file change", async () => {
  const old = deferred<imports.MasterImportPreview>();
  const post = vi.spyOn(imports, "previewMasterImport").mockReturnValueOnce(old.promise).mockResolvedValue({ ...preview, source_digest: "digest-2" });
  show(); await selectFile(); fireEvent.click(screen.getByRole("button", { name: "Vorschau prüfen" }));
  await selectFile("kind;key\nproperty;new", "new.csv");
  expect(post.mock.calls[0][1]?.aborted).toBe(true);
  await act(async () => old.resolve(preview));
  expect(screen.queryByRole("table", { name: "CSV-Vorschau" })).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", { name: /geprüften CSV-Zeilen/ })).not.toBeInTheDocument();
});
it("invalidates a pending apply and ignores its late success after input changes", async () => {
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview);
  const old = deferred<imports.MasterImportResult>();
  const apply = vi.spyOn(imports, "applyMasterImport").mockReturnValue(old.promise);
  const reload = show(); await cleanPreview(); confirm(); fireEvent.click(screen.getByRole("button", { name: "Stammdaten importieren" }));
  await selectFile("kind;key\nproperty;new", "new.csv");
  expect(apply.mock.calls[0][1]?.aborted).toBe(true);
  await act(async () => old.resolve(result));
  expect(screen.queryByRole("region", { name: "Importergebnis" })).not.toBeInTheDocument(); expect(reload).not.toHaveBeenCalled();
});
it("requires explicit admin consent and reason, sends only actor read/prepare/export and invalidates preview", async () => {
  const post = vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview);
  show(); await cleanPreview();
  const consent = screen.getByRole("checkbox", { name: /Datenbereich anlegen/ }); expect(consent).not.toBeChecked();
  fireEvent.click(consent); expect(screen.queryByRole("table", { name: "CSV-Vorschau" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Vorschau prüfen" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Begründung der Freigabe"), { target: { value: "Mein Verwaltungsauftrag" } });
  fireEvent.click(screen.getByRole("button", { name: "Vorschau prüfen" })); await screen.findByRole("table", { name: "CSV-Vorschau" });
  expect(post.mock.calls[1][0].data_audience).toEqual({ label: "CSV-Stammdatenimport", reason: "Mein Verwaltungsauftrag", grants: [{ user_id: "actor", capabilities: ["read", "prepare", "export"], reason: "Mein Verwaltungsauftrag" }] });
  fireEvent.change(screen.getByLabelText("Begründung der Freigabe"), { target: { value: "Anderer Auftrag" } });
  expect(screen.queryByRole("table", { name: "CSV-Vorschau" })).not.toBeInTheDocument();
});
it("does not allow clerks to self-grant and sends no silent audience", async () => {
  auth.role = "CLERK"; const post = vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview);
  show(); await cleanPreview(); expect(screen.queryByRole("checkbox", { name: /Datenbereich anlegen/ })).not.toBeInTheDocument();
  expect(post.mock.calls[0][0].data_audience).toBeNull();
});
it("rejects invalid UTF-8 and oversized files visibly without making requests", async () => {
  const post = vi.spyOn(imports, "previewMasterImport"); show();
  fireEvent.change(screen.getByLabelText("CSV-Datei"), { target: { files: [new File([new Uint8Array([255])], "bad.csv")] } });
  expect(await screen.findByRole("alert")).toHaveTextContent("UTF-8");
  expect(screen.getByRole("button", { name: "Vorschau prüfen" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("CSV-Datei"), { target: { files: [new File([new Uint8Array(262145)], "large.csv")] } });
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("256")); expect(post).not.toHaveBeenCalled();
});
it("prevents duplicate apply submissions while a request is pending", async () => {
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview); const pending = deferred<imports.MasterImportResult>();
  const apply = vi.spyOn(imports, "applyMasterImport").mockReturnValue(pending.promise);
  show(); await cleanPreview(); confirm(); const button = screen.getByRole("button", { name: "Stammdaten importieren" });
  fireEvent.click(button); fireEvent.click(button); expect(apply).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(result));
});
it.each([401, 403])("shows apply authorization error %s without pretending success", async status => {
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview);
  const apply = vi.spyOn(imports, "applyMasterImport").mockRejectedValueOnce(new ApiError(status, "Freigabe fehlt")).mockResolvedValue(result);
  const reload = show(); await cleanPreview(); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Stammdaten importieren" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Freigabe fehlt");
  expect(screen.queryByRole("region", { name: "Importergebnis" })).not.toBeInTheDocument(); expect(reload).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Import erneut versuchen" }));
  await screen.findByRole("region", { name: "Importergebnis" }); expect(apply.mock.calls[0][0]).toEqual(apply.mock.calls[1][0]);
});
it("retains an uncertain retry across closing and reopening the panel", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview);
  const apply = vi.spyOn(imports, "applyMasterImport").mockRejectedValueOnce(new Error("Antwort verloren")).mockResolvedValue(result);
  render(<MemoryRouter><PropertiesPage /></MemoryRouter>); await screen.findByText("Keine Objekte vorhanden.");
  const toggle = screen.getByRole("button", { name: "CSV-Stammdaten importieren" });
  fireEvent.click(toggle); await cleanPreview(); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Stammdaten importieren" })); await screen.findByRole("alert");
  fireEvent.click(toggle); expect(screen.queryByRole("button", { name: "Import erneut versuchen" })).not.toBeInTheDocument();
  fireEvent.click(toggle); fireEvent.click(screen.getByRole("button", { name: "Import erneut versuchen" }));
  await screen.findByRole("region", { name: "Importergebnis" }); expect(apply.mock.calls[0][0]).toEqual(apply.mock.calls[1][0]);
});
it("requires a new preview and confirmation and creates a new key after file changes", async () => {
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview);
  const apply = vi.spyOn(imports, "applyMasterImport").mockRejectedValueOnce(new Error("Antwort verloren")).mockResolvedValue(result);
  show(); await cleanPreview(); confirm(); fireEvent.click(screen.getByRole("button", { name: "Stammdaten importieren" })); await screen.findByRole("alert");
  await selectFile(csv + "\n", "revised.csv");
  expect(screen.queryByRole("button", { name: "Import erneut versuchen" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Vorschau prüfen" })); await screen.findByRole("table", { name: "CSV-Vorschau" });
  expect(screen.getByRole("checkbox", { name: /geprüften CSV-Zeilen/ })).not.toBeChecked();
  confirm(); fireEvent.click(screen.getByRole("button", { name: "Stammdaten importieren" })); await screen.findByRole("region", { name: "Importergebnis" });
  expect(apply.mock.calls[1][0].request_id).not.toBe(apply.mock.calls[0][0].request_id);
  expect(apply.mock.calls[1][0].csv_text).toBe(csv + "\n");
});
it("ignores a late preview after audience consent changes", async () => {
  const pending = deferred<imports.MasterImportPreview>(); const post = vi.spyOn(imports, "previewMasterImport").mockReturnValue(pending.promise);
  show(); await selectFile(); fireEvent.click(screen.getByRole("button", { name: "Vorschau prüfen" }));
  fireEvent.click(screen.getByRole("checkbox", { name: /Datenbereich anlegen/ }));
  expect(post.mock.calls[0][1]?.aborted).toBe(true); await act(async () => pending.resolve(preview));
  expect(screen.queryByRole("table", { name: "CSV-Vorschau" })).not.toBeInTheDocument();
});
it("renders CSV values and row errors as plain text", async () => {
  const text = '<img src=x onerror="alert(1)">';
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue({ ...preview, rows: [{ ...preview.rows[0], values: { name: text } }], errors: [{ line: 2, field: "name", message: text }] });
  const { container } = render(<MemoryRouter><MasterImport onImported={vi.fn()} /></MemoryRouter>);
  await cleanPreview(); expect(screen.getAllByText(text, { exact: false })).toHaveLength(2); expect(container.querySelector("img")).toBeNull();
});
it("reloads the paginated property list once after a successful import", async () => {
  const get = vi.spyOn(api, "get").mockResolvedValue({ items: [], next_cursor: null });
  vi.spyOn(imports, "previewMasterImport").mockResolvedValue(preview); vi.spyOn(imports, "applyMasterImport").mockResolvedValue(result);
  render(<MemoryRouter><PropertiesPage /></MemoryRouter>); await screen.findByText("Keine Objekte vorhanden.");
  fireEvent.click(screen.getByRole("button", { name: "CSV-Stammdaten importieren" })); await cleanPreview(); confirm();
  fireEvent.click(screen.getByRole("button", { name: "Stammdaten importieren" })); await screen.findByRole("region", { name: "Importergebnis" });
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  expect(get.mock.calls.every(([url]) => url.startsWith("/api/properties/page"))).toBe(true);
});
