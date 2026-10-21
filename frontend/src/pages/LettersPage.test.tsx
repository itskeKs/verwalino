import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AuthCtx } from "../auth/useAuth";
import * as letters from "../api/letters";
import * as endpoints from "../api/endpoints";
import { ApiError } from "../api/client";
import { LettersPage } from "./LettersPage";

const template = { id: "t1", name: "Hinweis", subject: "An {{mieter.name}}", body: "<script>alert(1)</script> {{objekt.adresse}}", created_at: "2026-10-21", updated_at: "2026-10-21" };
const document = { id: "d1", request_id: "r1", template_id: "t1", property_id: "p1", person_id: "person", recipient_name: "Anna Mieter", subject: "An Anna Mieter", as_of: "2026-10-21", created_at: "2026-10-21T12:00:00Z", pdf_url: "/api/letters/documents/d1/pdf" };
function show(role = "CLERK") {
  return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() }}><LettersPage /></AuthCtx.Provider>);
}
async function selectInputs() {
  await screen.findByRole("option", { name: "Hinweis" });
  fireEvent.change(screen.getByLabelText("Objekt *"), { target: { value: "p1" } });
  fireEvent.change(screen.getByLabelText("Vorlage *"), { target: { value: "t1" } });
  fireEvent.change(screen.getByLabelText("Stichtag *"), { target: { value: "2026-10-21" } });
  await waitFor(() => expect(screen.getByRole("button", { name: "Briefe erstellen" })).toBeEnabled());
}
beforeEach(() => {
  vi.spyOn(endpoints, "listProperties").mockResolvedValue([{ id: "p1", name: "Gartenhaus" }, { id: "p2", name: "Haupthaus" }] as never);
  vi.spyOn(letters, "listLetterTemplates").mockResolvedValue([template]);
  vi.spyOn(letters, "listLetterDocuments").mockResolvedValue([document]);
  vi.spyOn(letters, "createLetterTemplate").mockResolvedValue({ ...template, id: "t2", name: "Neue Vorlage" });
  vi.spyOn(letters, "updateLetterTemplate").mockResolvedValue({ ...template, body: "Geändert" });
  vi.spyOn(letters, "issueLetters").mockResolvedValue([document]);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it("requires explicit inputs and confirmation, then shows retained recipient PDFs", async () => {
  show();
  expect(screen.getByLabelText("Stichtag *")).toHaveValue("");
  expect(screen.getByRole("button", { name: "Briefe erstellen" })).toBeDisabled();
  await selectInputs();
  expect(screen.getByText(template.body)).toBeVisible();
  expect(screen.getByText(/Kein HTML/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Briefe erstellen" }));
  const dialog = screen.getByRole("dialog", { name: "Briefe verbindlich erstellen?" });
  expect(within(dialog).getByText(/Gartenhaus/)).toBeVisible();
  expect(letters.issueLetters).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Jetzt erstellen" }));
  await waitFor(() => expect(letters.issueLetters).toHaveBeenCalledWith({ request_id: expect.any(String), template_id: "t1", property_id: "p1", as_of: "2026-10-21" }));
  const link = await screen.findByRole("link", { name: "PDF für Anna Mieter" });
  expect(link).toHaveAttribute("href", document.pdf_url); expect(link).toHaveAttribute("target", "_blank"); expect(link).toHaveAttribute("rel", "noopener noreferrer");
});
it.each(["READ_ONLY", "UNKNOWN"])("permits history and plaintext template viewing but no writes for %s", async role => {
  show(role); await screen.findByRole("option", { name: "Hinweis" });
  fireEvent.change(screen.getByLabelText("Objekt *"), { target: { value: "p1" } });
  fireEvent.change(screen.getByLabelText("Vorlage *"), { target: { value: "t1" } });
  expect(await screen.findByRole("link", { name: "PDF für Anna Mieter" })).toBeVisible();
  expect(screen.getByText(template.body)).toBeVisible();
  for (const name of ["Briefe erstellen", "Neue Vorlage", "Vorlage bearbeiten"]) expect(screen.queryByRole("button", { name })).toBeNull();
});
it("creates and updates saved templates without changing issued history", async () => {
  show(); await selectInputs(); await screen.findByRole("link", { name: "PDF für Anna Mieter" });
  fireEvent.click(screen.getByRole("button", { name: "Neue Vorlage" }));
  fireEvent.change(screen.getByLabelText("Vorlagenname *"), { target: { value: "Neue Vorlage" } });
  fireEvent.change(screen.getByLabelText("Betreff *"), { target: { value: "Hallo {{mieter.name}}" } });
  fireEvent.change(screen.getByLabelText("Brieftext *"), { target: { value: "<b>Nur Text</b>" } });
  fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));
  await waitFor(() => expect(letters.createLetterTemplate).toHaveBeenCalledWith({ name: "Neue Vorlage", subject: "Hallo {{mieter.name}}", body: "<b>Nur Text</b>" }));
  await waitFor(() => expect(screen.queryByLabelText("Vorlagenname *")).toBeNull());
  fireEvent.change(screen.getByLabelText("Vorlage *"), { target: { value: "t1" } });
  fireEvent.click(screen.getByRole("button", { name: "Vorlage bearbeiten" }));
  fireEvent.change(screen.getByLabelText("Brieftext *"), { target: { value: "Geändert" } });
  fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));
  await waitFor(() => expect(letters.updateLetterTemplate).toHaveBeenCalledWith("t1", { name: template.name, subject: template.subject, body: "Geändert" }));
  expect(screen.getByRole("link", { name: "PDF für Anna Mieter" })).toHaveAttribute("href", document.pdf_url);
});
it("reloads persistent history from the server on remount", async () => {
  const first = show(); await selectInputs(); await screen.findByRole("link", { name: "PDF für Anna Mieter" }); first.unmount();
  show(); await selectInputs(); expect(await screen.findByRole("link", { name: "PDF für Anna Mieter" })).toBeVisible();
  expect(letters.listLetterDocuments).toHaveBeenCalledTimes(2); expect(letters.issueLetters).not.toHaveBeenCalled();
});
it("preserves request ID on uncertain failure and replaces it after inputs change", async () => {
  vi.mocked(letters.issueLetters).mockRejectedValue(new TypeError("offline"));
  show(); await selectInputs();
  async function issue() {
    fireEvent.click(screen.getByRole("button", { name: "Briefe erstellen" }));
    fireEvent.click(screen.getByRole("button", { name: "Jetzt erstellen" }));
    await screen.findByText(/offline/);
  }
  await issue(); const first = vi.mocked(letters.issueLetters).mock.calls[0][0].request_id;
  fireEvent.click(screen.getByRole("button", { name: "Vorlage bearbeiten" }));
  fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
  await issue(); expect(vi.mocked(letters.issueLetters).mock.calls[1][0].request_id).toBe(first);
  fireEvent.change(screen.getByLabelText("Stichtag *"), { target: { value: "2026-10-22" } });
  await issue(); expect(vi.mocked(letters.issueLetters).mock.calls[2][0].request_id).not.toBe(first);
});
it("locks issue inputs and duplicate clicks synchronously while busy", async () => {
  let finish!: (value: typeof document[]) => void;
  vi.mocked(letters.issueLetters).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  show(); await selectInputs(); fireEvent.click(screen.getByRole("button", { name: "Briefe erstellen" }));
  const confirm = screen.getByRole("button", { name: "Jetzt erstellen" }); fireEvent.click(confirm); fireEvent.click(confirm);
  expect(letters.issueLetters).toHaveBeenCalledOnce();
  for (const name of ["Objekt *", "Vorlage *", "Stichtag *"]) expect(screen.getByLabelText(name)).toBeDisabled();
  expect(screen.getByRole("button", { name: "Abbrechen" })).toBeDisabled();
  await act(async () => finish([document]));
});
it("ignores obsolete history after switching properties and retries a history error", async () => {
  let finish!: (value: typeof document[]) => void;
  vi.mocked(letters.listLetterDocuments).mockReturnValueOnce(new Promise(resolve => { finish = resolve; })).mockRejectedValueOnce(new ApiError(403, "Kein Zugriff"));
  show(); await selectInputs(); fireEvent.change(screen.getByLabelText("Objekt *"), { target: { value: "p2" } });
  await screen.findByText("Kein Zugriff");
  await act(async () => finish([document]));
  expect(screen.queryByRole("link", { name: "PDF für Anna Mieter" })).toBeNull();
  vi.mocked(letters.listLetterDocuments).mockResolvedValue([]);
  fireEvent.click(screen.getByRole("button", { name: "Historie erneut laden" }));
  expect(await screen.findByText("Noch keine Briefe für dieses Objekt.")).toBeVisible();
});
it("retains template form after save failure and retries initial loading", async () => {
  vi.mocked(letters.listLetterTemplates).mockRejectedValueOnce(new ApiError(500, "Vorlagen nicht verfügbar"));
  show(); await screen.findByText("Vorlagen nicht verfügbar");
  fireEvent.click(screen.getByRole("button", { name: "Erneut laden" })); await selectInputs();
  vi.mocked(letters.updateLetterTemplate).mockRejectedValueOnce(new ApiError(422, "Ungültige Platzhalter"));
  fireEvent.click(screen.getByRole("button", { name: "Vorlage bearbeiten" }));
  fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));
  await screen.findByText("Ungültige Platzhalter"); expect(screen.getByLabelText("Brieftext *")).toHaveValue(template.body);
  fireEvent.click(screen.getByRole("button", { name: "Vorlage speichern" }));
  await waitFor(() => expect(letters.updateLetterTemplate).toHaveBeenCalledTimes(2));
});
