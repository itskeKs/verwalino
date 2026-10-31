import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError, uploadFormData } from "../api/client";
import { OrganizationDataCard } from "./OrganizationDataCard";

vi.mock("../api/client", async original => ({ ...await original<typeof import("../api/client")>(),
  api: { getBlob: vi.fn(), get: vi.fn(), post: vi.fn() }, uploadFormData: vi.fn() }));

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:organization"), revokeObjectURL: vi.fn() }));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  vi.mocked(api.getBlob).mockReset();
  vi.mocked(api.get).mockReset().mockResolvedValue({ id: "org", name: "Verwaltung", import_available: false, deletion: null });
  vi.mocked(api.post).mockReset();
  vi.mocked(uploadFormData).mockReset();
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("downloads the real archive only on explicit request", async () => {
  vi.mocked(api.getBlob).mockResolvedValue(new Blob(["archive"]));
  render(<OrganizationDataCard />);
  expect(api.getBlob).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Daten exportieren" }));
  await waitFor(() => expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1));
  expect(api.getBlob).toHaveBeenCalledWith("/api/organizations/export", { method: "POST", signal: expect.any(AbortSignal) });
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:organization");
});

it("preserves the server refusal and allows retry without a false download", async () => {
  vi.mocked(api.getBlob).mockRejectedValue(new ApiError(429, "Bitte warten Sie eine Minute."));
  render(<OrganizationDataCard />);
  fireEvent.click(screen.getByRole("button", { name: "Daten exportieren" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Bitte warten Sie eine Minute.");
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Daten exportieren" })).toBeEnabled();
});

it("blocks repeated requests and ignores a result after unmount", async () => {
  let complete!: (blob: Blob) => void;
  vi.mocked(api.getBlob).mockImplementation(() => new Promise(resolve => { complete = resolve; }));
  const view = render(<OrganizationDataCard />);
  const button = screen.getByRole("button", { name: "Daten exportieren" });
  fireEvent.click(button); fireEvent.click(button);
  expect(api.getBlob).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Export wird vorbereitet …" })).toBeDisabled();
  view.unmount();
  await act(async () => { complete(new Blob(["late"])); });
  expect(HTMLAnchorElement.prototype.click).not.toHaveBeenCalled();
});

it("offers import only for an empty organization and keeps the selected file on refusal", async () => {
  vi.mocked(api.get).mockResolvedValue({ id: "org", name: "Verwaltung", import_available: true, deletion: null });
  vi.mocked(uploadFormData).mockRejectedValue(new ApiError(422, "Fehlende Abhängigkeiten: person (2)."));
  render(<OrganizationDataCard />);
  const input = await screen.findByLabelText("Exportarchiv");
  const file = new File(["archive"], "organisation.tar.gz", { type: "application/gzip" });
  fireEvent.change(input, { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Daten importieren" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Fehlende Abhängigkeiten");
  expect((input as HTMLInputElement).files?.[0]).toBe(file);
});

it("puts import entity names and source IDs in a technical disclosure", async () => {
  vi.mocked(api.get).mockResolvedValue({ id: "org", name: "Verwaltung", import_available: true, deletion: null });
  vi.mocked(uploadFormData).mockRejectedValue(Object.assign(new ApiError(422, "Im Archiv fehlen benötigte Abhängigkeiten."), {
    technicalDetails: "person_data_domain: 1 (synthetic-source-id)",
  }));
  render(<OrganizationDataCard />);
  fireEvent.change(await screen.findByLabelText("Exportarchiv"), { target: { files: [new File(["archive"], "organisation.tar.gz")] } });
  fireEvent.click(screen.getByRole("button", { name: "Daten importieren" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Im Archiv fehlen benötigte Abhängigkeiten.");
  const technical = screen.getByText(/person_data_domain/).closest("details");
  expect(technical).not.toHaveAttribute("open");
  expect(screen.getByText("Technische Details")).toBeVisible();
});

it("shows retention, strongly suggests a fresh export and requires the exact organization name", async () => {
  vi.mocked(api.get).mockResolvedValue({ id: "org", name: "Lange Verwaltung GmbH", import_available: false, deletion: null });
  vi.mocked(api.post).mockResolvedValue({ status: "requested", organization_name: "Lange Verwaltung GmbH", notice: "Nach der Löschung ist keine Anmeldung mehr möglich; aufbewahrungspflichtige Unterlagen bleiben für acht Jahre beim Betreiber.", retention: { retained: { audit_event: { count: 4, until: "2034-12-31" } }, erased: { person: 2 }, erased_fields: ["Ungebundene Personendaten"] } });
  render(<OrganizationDataCard />);
  fireEvent.click(await screen.findByRole("button", { name: "Organisation löschen" }));
  const confirm = await screen.findByRole("button", { name: "Löschung bestätigen" });
  expect(confirm).toBeDisabled();
  expect(screen.getByText(/Nach der Löschung ist keine Anmeldung/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("Organisationsname zur Bestätigung"), { target: { value: "Lange Verwaltung GmbH" } });
  expect(confirm).toBeEnabled();
  expect(screen.getByRole("button", { name: "Jetzt frischen Export erstellen" })).toBeVisible();
});
