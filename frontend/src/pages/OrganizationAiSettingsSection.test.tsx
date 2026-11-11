import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import { OrganizationAiSettingsSection } from "./OrganizationAiSettingsSection";

const settings = {
  provider: "openai", base_url: null, key_set: true, model: "evaluated-model",
  import_enabled: false, qa_enabled: true, monthly_limit_usd: "5.00", price_known: true,
  request_timeout_seconds: null,
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("saves the entered key and limit and clears the key field afterwards", async () => {
  vi.spyOn(api, "get").mockResolvedValue(settings as never);
  const put = vi.spyOn(api, "put").mockImplementation(async (_url, body) => ({ ...settings, ...(body as object), key_set: true }) as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  const keyInput = await screen.findByLabelText("API-Schlüssel");
  expect(screen.getByLabelText("Monatliches Limit in USD (optional)")).toHaveValue(5);
  fireEvent.change(keyInput, { target: { value: "sk-neu" } });
  fireEvent.change(screen.getByLabelText("Monatliches Limit in USD (optional)"), { target: { value: "12.50" } });
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));

  expect(await screen.findByText(/KI-Einstellungen gespeichert/)).toBeVisible();
  expect(put).toHaveBeenCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({
    api_key: "sk-neu", monthly_limit_usd: "12.50", model: "evaluated-model", qa_enabled: true,
  }));
  expect(keyInput).toHaveValue("");
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
