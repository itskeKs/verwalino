import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import { OrganizationAiSettingsSection } from "./OrganizationAiSettingsSection";

const confirmation = { confirmed_by_name: "Admin Alpha", confirmed_at: "2026-11-13T10:00:00+00:00" };

const settings = {
  provider: "openai", base_url: null, key_set: true, model: "evaluated-model",
  document_model: "reading-model", document_price_known: true,
  import_enabled: false, qa_enabled: true, monthly_limit_usd: "5.00", price_known: true,
  request_timeout_seconds: null, confirmation, confirmation_required: false, invoice_processing_enabled: true, invoice_confirmation_required: false, inbox_confirmation_required: false,
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
  fireEvent.click(screen.getByLabelText("Teilungserklärungen importieren sowie Rechnungen und Dokumente im Posteingang auswerten"));
  expect(screen.queryByText(/fehlt noch ein Modell unter „Dokumente einlesen“/)).not.toBeInTheDocument();
});

it("clears both model choices when the provider or the endpoint changes", async () => {
  vi.spyOn(api, "get").mockResolvedValue(settings as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  fireEvent.change(await screen.findByLabelText("Anbieter"), { target: { value: "openai_compatible" } });
  expect(screen.getByLabelText("Dokumente einlesen")).toHaveValue("");
  expect(screen.getByLabelText("Informationen extrahieren")).toHaveValue("");
});

it("says that the switch, its description and the confirmation cover invoices and the documents of the inbox", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ ...settings, confirmation_required: true, confirmation: null } as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  const toggle = await screen.findByLabelText("Teilungserklärungen importieren sowie Rechnungen und Dokumente im Posteingang auswerten");
  expect(toggle).toHaveAccessibleDescription(/Rechnungen und Dokumente im Posteingang ohne strukturierte E-Rechnungsdaten \(zum Beispiel Scans\) an den gewählten Anbieter.*XRechnung oder ZUGFeRD werden direkt gelesen und nicht übermittelt.*Rechnungen und Dokumente im Posteingang werden erst übermittelt, wenn Ihre Organisation den aktuellen Bestätigungstext angenommen hat/s);
  expect(screen.getByText(/Das gilt auch für Rechnungen und für Dokumente im Posteingang: Hochgeladene Rechnungen und Dokumente \(PDF, PNG oder JPEG\)/)).toBeVisible();
  expect(screen.getByLabelText(/Dokumente \(Teilungserklärungen, Rechnungen und Dokumente im Posteingang\) und extrahierte Texte/)).toBeInTheDocument();
});

it("asks again for invoices when the stored confirmation predates them, and leaves the declaration import as it is", async () => {
  const earlier = { ...settings, import_enabled: true, invoice_confirmation_required: true, inbox_confirmation_required: true };
  vi.spyOn(api, "get").mockResolvedValue(earlier as never);
  const put = vi.spyOn(api, "put").mockImplementation(async (_url, body) => ({
    ...earlier, ...(body as object), key_set: true,
    invoice_confirmation_required: (body as { confirmation?: boolean }).confirmation !== true,
    inbox_confirmation_required: (body as { confirmation?: boolean }).confirmation !== true,
    confirmation: { confirmed_by_name: "Admin Beta", confirmed_at: "2026-11-21T10:00:00+00:00" } }) as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  expect(await screen.findByText(/galt, bevor Rechnungen und Dokumente im Posteingang ausgewertet wurden.*Teilungserklärungen sind davon nicht betroffen.*Rechnungen und Dokumente im Posteingang werden erst nach einer neuen Bestätigung an den Anbieter übermittelt/s)).toBeVisible();
  expect(screen.queryByText(/^Bestätigt am/)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Teilungserklärungen importieren sowie Rechnungen und Dokumente im Posteingang auswerten")).toBeChecked();

  // Saving without the tick sends no confirmation and keeps invoices on the host.
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));
  expect(await screen.findByText(/KI-Einstellungen gespeichert/)).toBeVisible();
  expect(put).toHaveBeenLastCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ confirmation: false, import_enabled: true }));
  expect(put.mock.calls[0][1]).not.toHaveProperty("api_key");
  expect(screen.getByText(/galt, bevor Rechnungen und Dokumente im Posteingang ausgewertet wurden/)).toBeVisible();

  // The tick confirms the current text without entering the key again.
  fireEvent.click(screen.getByLabelText(/Anbieter selbst gewählt/));
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));
  expect(await screen.findByText(/Bestätigt am .* von Admin Beta\./)).toBeVisible();
  expect(put).toHaveBeenLastCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ confirmation: true }));
  expect(put.mock.calls[1][1]).not.toHaveProperty("api_key");
  expect(screen.queryByText(/galt, bevor/)).not.toBeInTheDocument();
});

it("keeps what the stored confirmation covered and asks once more only for the documents of the inbox, without the key", async () => {
  // The text of version 2 named declarations and invoices: they stay, the inbox is held back until the current text is confirmed.
  const invoicesOnly = { ...settings, import_enabled: true, invoice_confirmation_required: false, inbox_confirmation_required: true };
  vi.spyOn(api, "get").mockResolvedValue(invoicesOnly as never);
  const put = vi.spyOn(api, "put").mockImplementation(async (_url, body) => ({
    ...invoicesOnly, ...(body as object), key_set: true, inbox_confirmation_required: (body as { confirmation?: boolean }).confirmation !== true,
    confirmation: { confirmed_by_name: "Admin Beta", confirmed_at: "2026-11-22T10:00:00+00:00" } }) as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  expect(await screen.findByText(/galt, bevor Dokumente im Posteingang ausgewertet wurden\. Teilungserklärungen und Rechnungen sind davon nicht betroffen.*Dokumente im Posteingang werden erst nach einer neuen Bestätigung an den Anbieter übermittelt.*Den Schlüssel müssen Sie dafür nicht erneut eingeben/s)).toBeVisible();
  expect(screen.queryByText(/galt, bevor Rechnungen und Dokumente/)).not.toBeInTheDocument();
  expect(screen.queryByText(/^Bestätigt am/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByLabelText(/Anbieter selbst gewählt/));
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));
  expect(await screen.findByText(/Bestätigt am .* von Admin Beta\./)).toBeVisible();
  expect(put).toHaveBeenLastCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ confirmation: true }));
  expect(put.mock.calls[0][1]).not.toHaveProperty("api_key");
  expect(screen.queryByText(/galt, bevor/)).not.toBeInTheDocument();
});

// Where this installation does not process invoices (HV_INVOICE_EXTRACTION_ENABLED=false, the portable stack) the card
// is about the declaration import alone: it says nothing about invoices being evaluated or sent.
const withoutInvoices = { ...settings, invoice_processing_enabled: false };

it("claims nothing about invoices before the settings say whether this installation processes them", async () => {
  vi.spyOn(api, "get").mockReturnValue(new Promise(() => {}) as never);
  const { container } = render(<OrganizationAiSettingsSection organizationId="org-1" />);

  expect(await screen.findByRole("status")).toHaveTextContent("KI-Einstellungen werden geladen");
  expect(container).toHaveTextContent("Bei aktivierten Funktionen werden die benötigten Dokumentinhalte");
  expect(container).not.toHaveTextContent(/Rechnung|Posteingang/);
});

it("names no invoices and no inbox documents in the switch, its description, the hints or the confirmation where they are not processed", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ ...withoutInvoices, import_enabled: true, document_model: null, document_price_known: false, confirmation_required: true, confirmation: null } as never);
  const { container } = render(<OrganizationAiSettingsSection organizationId="org-1" />);

  const toggle = await screen.findByLabelText("Teilungserklärungen importieren");
  expect(toggle).toBeChecked();
  expect(toggle).not.toHaveAttribute("aria-describedby");
  expect(container.querySelector("#ai-import-info")).toBeNull();
  expect(screen.getByText(/Für den Import von Teilungserklärungen fehlt noch ein Modell unter „Dokumente einlesen“/)).toBeVisible();
  expect(screen.getByLabelText(/Anbieter selbst gewählt/).closest("label")).toHaveTextContent("Dokumente und extrahierte Texte an den gewählten Anbieter zu übermitteln.");
  expect(container).not.toHaveTextContent(/Rechnung|Posteingang/);
});

it("stores the confirmation without a word about invoices where they are not processed, and asks nothing more about them", async () => {
  vi.spyOn(api, "get").mockResolvedValue({ ...withoutInvoices, confirmation_required: true, confirmation: null } as never);
  const put = vi.spyOn(api, "put").mockImplementation(async (_url, body) => ({ ...withoutInvoices, ...(body as object), confirmation }) as never);
  render(<OrganizationAiSettingsSection organizationId="org-1" />);

  fireEvent.click(await screen.findByLabelText(/Anbieter selbst gewählt/));
  fireEvent.click(screen.getByRole("button", { name: "KI-Einstellungen speichern" }));
  expect(await screen.findByText(/Bestätigt am .* von Admin Alpha\./)).toBeVisible();
  expect(put).toHaveBeenLastCalledWith("/api/organizations/org-1/ai-settings", expect.objectContaining({ confirmation: true }));
  expect(screen.queryByLabelText(/Anbieter selbst gewählt/)).not.toBeInTheDocument();
});

it("shows the invoice wording again once the settings say that invoices are processed", async () => {
  vi.spyOn(api, "get").mockResolvedValueOnce(withoutInvoices as never).mockResolvedValueOnce({ ...settings, invoice_confirmation_required: true, inbox_confirmation_required: true } as never);
  const { unmount } = render(<OrganizationAiSettingsSection organizationId="org-1" />);
  expect(await screen.findByLabelText("Teilungserklärungen importieren")).toBeInTheDocument();
  unmount();

  render(<OrganizationAiSettingsSection organizationId="org-1" />);
  expect(await screen.findByLabelText("Teilungserklärungen importieren sowie Rechnungen und Dokumente im Posteingang auswerten")).toBeInTheDocument();
  expect(screen.getByLabelText(/Dokumente \(Teilungserklärungen, Rechnungen und Dokumente im Posteingang\) und extrahierte Texte/)).toBeInTheDocument();
});
