import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import { OrganizationAiSettingsSection } from "./OrganizationAiSettingsSection";

const confirmation = { confirmed_by_name: "Admin Alpha", confirmed_at: "2026-11-13T10:00:00+00:00" };

const settings = {
  provider: "openai", base_url: null, key_set: true, model: "evaluated-model",
  document_model: "reading-model", document_price_known: true,
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

const listed = {
  models: [
    { id: "evaluated-model", evaluated: true, price_known: true },
    { id: "reading-model", evaluated: false, price_known: true },
    { id: "future-model", evaluated: false, price_known: false },
  ],
};

it("offers one provider model list for reading and extraction and keeps the other choice on every save", async () => {
  vi.spyOn(api, "get").mockResolvedValue(settings as never);
  vi.spyOn(api, "post").mockResolvedValue(listed as never);
  const put = vi.spyOn(api, "put").mockImplementation(async (_url, body) => ({ ...settings, ...(body as object) }) as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  const reading = await screen.findByLabelText("Dokumente einlesen");
  const extraction = screen.getByLabelText("Informationen extrahieren");
  expect(reading).toHaveValue("reading-model");
  expect(extraction).toHaveValue("evaluated-model");
  fireEvent.click(screen.getByRole("button", { name: "Modelle laden" }));
  await within(reading).findByRole("option", { name: "future-model" });
  for (const select of [reading, extraction]) {
    expect(within(select).getAllByRole("option").map(option => option.textContent)).toEqual(
      expect.arrayContaining(["evaluated-model · geprüft", "reading-model", "future-model"]));
  }

  fireEvent.change(reading, { target: { value: "future-model" } });
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));
  await screen.findByText(/KI-Einstellungen gespeichert/);
  expect(put).toHaveBeenLastCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ model: "evaluated-model", document_model: "future-model" }));

  fireEvent.change(extraction, { target: { value: "reading-model" } });
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));
  await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(2));
  expect(put).toHaveBeenLastCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ model: "reading-model", document_model: "future-model" }));

  // The same model may serve both roles.
  fireEvent.change(reading, { target: { value: "reading-model" } });
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));
  await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(3));
  expect(put).toHaveBeenLastCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ model: "reading-model", document_model: "reading-model" }));
});

it("states the price status of the reading model apart from the extraction model", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ ...settings, price_known: true, document_price_known: false } as never);
  vi.spyOn(api, "post").mockResolvedValue(listed as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  expect(await screen.findByText(/Für das Modell „Dokumente einlesen“ ist kein Preis bekannt/)).toBeVisible();
  expect(screen.queryByText(/Für das Modell „Informationen extrahieren“ ist kein Preis bekannt/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Modelle laden" }));
  await within(screen.getByLabelText("Dokumente einlesen")).findByRole("option", { name: "future-model" });
  fireEvent.change(screen.getByLabelText("Dokumente einlesen"), { target: { value: "reading-model" } });
  fireEvent.change(screen.getByLabelText("Informationen extrahieren"), { target: { value: "future-model" } });
  expect(screen.queryByText(/Für das Modell „Dokumente einlesen“ ist kein Preis bekannt/)).not.toBeInTheDocument();
  expect(screen.getByText(/Für das Modell „Informationen extrahieren“ ist kein Preis bekannt/)).toBeVisible();
});

it("explains in German that the import needs the reading model while it is not chosen", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ ...settings, import_enabled: true, document_model: null, document_price_known: false } as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  expect(await screen.findByLabelText("Dokumente einlesen")).toHaveValue("");
  expect(screen.getByText(/Für den Import von Teilungserklärungen fehlt noch ein Modell unter „Dokumente einlesen“/)).toBeVisible();
  fireEvent.click(screen.getByLabelText("Teilungserklärungen importieren"));
  expect(screen.queryByText(/fehlt noch ein Modell unter „Dokumente einlesen“/)).not.toBeInTheDocument();
});

it("clears both model choices when the provider or the endpoint changes", async () => {
  vi.spyOn(api, "get").mockResolvedValue(settings as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  fireEvent.change(await screen.findByLabelText("Anbieter"), { target: { value: "openai_compatible" } });
  expect(screen.getByLabelText("Dokumente einlesen")).toHaveValue("");
  expect(screen.getByLabelText("Informationen extrahieren")).toHaveValue("");
});
