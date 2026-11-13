import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import { OrganizationAiSettingsSection } from "./OrganizationAiSettingsSection";

const confirmation = { confirmed_by_name: "Admin Alpha", confirmed_at: "2026-11-13T10:00:00+00:00" };

const settings = {
  provider: "openai", base_url: null, key_set: true, model: "evaluated-model",
  import_enabled: false, qa_enabled: true, monthly_limit_usd: "5.00", price_known: true,
  request_timeout_seconds: null, confirmation, confirmation_required: false,
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("refuses to store a new key without the provider confirmation and stores it with the tick", async () => {
  vi.spyOn(api, "get").mockResolvedValue(settings as never);
  const put = vi.spyOn(api, "put")
    .mockRejectedValueOnce(new ApiError(422,
      "Bitte bestätigen Sie vor dem Speichern, dass Ihre Organisation den Anbieter selbst beauftragt hat.",
      "ORGANIZATION_AI_CONFIRMATION_REQUIRED"))
    .mockImplementation(async (_url, body) => ({ ...settings, ...(body as object), key_set: true }) as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  const keyInput = await screen.findByLabelText("API-Schlüssel");
  fireEvent.change(keyInput, { target: { value: "sk-neu" } });
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));

  expect(await screen.findByText(/Bitte bestätigen Sie vor dem Speichern, dass Ihre Organisation/)).toBeVisible();
  expect(put).toHaveBeenCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ api_key: "sk-neu", confirmation: false }));

  fireEvent.click(screen.getByLabelText(/Anbieter selbst gewählt/));
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));

  expect(await screen.findByText(/KI-Einstellungen gespeichert/)).toBeVisible();
  expect(put).toHaveBeenLastCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ api_key: "sk-neu", confirmation: true }));
  expect(keyInput).toHaveValue("");
});

it("refuses a legacy stored key saved without the tick, with the message at the field and no success message", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ ...settings, confirmation_required: true, confirmation: null } as never);
  const put = vi.spyOn(api, "put").mockRejectedValue(new ApiError(422,
    "Bitte bestätigen Sie vor dem Speichern, dass Ihre Organisation den Anbieter selbst beauftragt hat.",
    "ORGANIZATION_AI_CONFIRMATION_REQUIRED"));
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  fireEvent.click(await screen.findByRole("button", { name: "KI-Einstellungen speichern" }));

  expect(await screen.findByText(/Bitte bestätigen Sie vor dem Speichern, dass Ihre Organisation/)).toBeVisible();
  expect(screen.queryByText(/KI-Einstellungen gespeichert/)).not.toBeInTheDocument();
  expect(put).toHaveBeenCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ confirmation: false }));
});

it("names the confirmer and time for a confirmed key and shows the confirmation for a legacy stored key", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce(settings as never)
    .mockResolvedValueOnce({ ...settings, confirmation_required: true, confirmation: null } as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);
  expect(await screen.findByText(/Bestätigt am .* von Admin Alpha\./)).toBeVisible();

  cleanup();
  render(<OrganizationAiSettingsSection organizationId="org-1" />);
  expect(await screen.findByLabelText(/Anbieter selbst gewählt/)).toBeInTheDocument();
  expect(screen.queryByText(/Bestätigt am/)).not.toBeInTheDocument();
});

it("offers a reload when the settings cannot be loaded", async () => {
  const get = vi.spyOn(api, "get")
    .mockRejectedValueOnce(new ApiError(500, "down"))
    .mockResolvedValueOnce(settings as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  expect(await screen.findByRole("alert")).toHaveTextContent("Die KI-Einstellungen konnten nicht geladen werden.");
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" }));
  expect(await screen.findByLabelText("API-Schlüssel")).toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(2);
});
