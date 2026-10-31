import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { OrganizationDataCard } from "./OrganizationDataCard";

vi.mock("../api/client", async original => ({ ...await original<typeof import("../api/client")>(),
  api: { getBlob: vi.fn() } }));

beforeEach(() => {
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:organization"), revokeObjectURL: vi.fn() }));
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  vi.mocked(api.getBlob).mockReset();
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
