import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AuthCtx } from "../auth/useAuth";
import type { BankAccount, Property } from "../api/types";
import { ApiError } from "../api/client";
import * as sepa from "../api/sepa";
import { SepaExport } from "./SepaExport";

vi.mock("./AccountingScopeSelector", () => ({ AccountingScopeSelector: ({ propertyId, capability, onChange }: { propertyId: string; capability: string; onChange: (id: string | null) => void }) => <select aria-label={`Abrechnungskreis ${propertyId} ${capability}`} onChange={e => onChange(e.target.value || null)}><option value="">Ganzes Objekt</option><option value="scope">Kreis</option></select> }));
const properties = [{ id: "p1", name: "Haus" }] as Property[];
const bankAccounts = [{ id: "b1", label: "Mietkonto", property_id: "p1", iban: "DE89370400440532013000" }, { id: "b2", label: "Anderes Konto", property_id: "p1" }] as BankAccount[];
const account: sepa.SepaAccount = { bank: { id: "b1", label: "Mietkonto", property_id: "p1", iban: "DE89370400440532013000", bic: null }, creditor: { creditor_id: "DE98ZZZ09999999999", name: "Vermieter", country: "DE", town: "Berlin", bank_authorized: true }, tenants: [{ tenant_profile_id: "t1", person_id: "person", name: "Mieter", iban: "DE89370400440532013000", city: "Berlin" }], mandates: [{ id: "m1", tenant_profile_id: "t1", reference: "SIGNED-1", signed_on: "2026-01-01", sequence_type: "FRST", last_used_on: null, debtor_country: "DE", original_reference: "Archiv 42", revoked_at: null, source_current: true }] };
const preview: sepa.SepaPreview = { rows: [{ tenant_profile_id: "t1", name: "Mieter", mandate_id: "m1", reference: "SIGNED-1", amount: "12345678901234567890.91", sequence_type: "FRST", lease_ids: ["lease"] }], total: "12345678901234567890.91", source_digest: "digest" };
let click: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  vi.spyOn(sepa, "getSepaAccount").mockResolvedValue(account);
  vi.spyOn(sepa, "previewSepa").mockResolvedValue(preview);
  vi.spyOn(sepa, "downloadSepaXml").mockResolvedValue(new Blob(["<Document/>"]));
  vi.spyOn(sepa, "saveSepaCreditor").mockResolvedValue(undefined);
  vi.spyOn(sepa, "createSepaMandate").mockResolvedValue(undefined);
  vi.spyOn(sepa, "updateSepaMandate").mockResolvedValue(undefined);
  vi.spyOn(sepa, "revokeSepaMandate").mockResolvedValue(undefined);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:sepa"), revokeObjectURL: vi.fn() }));
  click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
function show(role = "OWNER_ADMIN") { return render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role }, login: vi.fn(), logout: vi.fn() }}><SepaExport bankAccounts={bankAccounts} properties={properties} /></AuthCtx.Provider>); }
function change(label: string, value: string) { fireEvent.change(screen.getByLabelText(label), { target: { value } }); }
async function load() { change("SEPA-Bankkonto *", "b1"); fireEvent.click(screen.getByRole("button", { name: "SEPA-Konfiguration laden" })); await screen.findByRole("region", { name: "Mandat SIGNED-1" }); }
async function ready() {
  await load();
  change("Jahr *", "2026"); change("Monat *", "10"); change("Einzugsdatum *", "2026-10-20"); change("Nachrichten-ID *", "OCT-2026"); change("Vorabankündigung erfolgt am *", "2026-10-06"); change("Ankündigungsfrist in Tagen *", "14");
  fireEvent.click(screen.getByLabelText(/Die tatsächliche Vorabankündigung/)); fireEvent.click(screen.getByLabelText(/Bankprofil pain/));
  fireEvent.click(screen.getByRole("button", { name: "SEPA-Vorschau erstellen" })); await screen.findByRole("table", { name: "SEPA-Vorschau" });
}
it("loads nothing automatically and starts all new fields blank", async () => {
  vi.mocked(sepa.getSepaAccount).mockResolvedValue({ ...account, creditor: null, mandates: [] }); show();
  expect(sepa.getSepaAccount).not.toHaveBeenCalled(); expect(screen.getByLabelText("SEPA-Bankkonto *")).toHaveValue("");
  change("SEPA-Bankkonto *", "b1"); fireEvent.click(screen.getByRole("button", { name: "SEPA-Konfiguration laden" }));
  expect(await screen.findByLabelText("Gläubiger-Land (ISO) *")).toHaveValue("");
  for (const label of ["Gläubiger-ID *", "Gläubigername *", "Gläubigerort *", "Schuldner-Land (ISO) *", "Mandatsreferenz *", "Original-Aufbewahrungsreferenz *", "IBAN ausdrücklich bestätigen *"]) expect(screen.getByLabelText(label)).toHaveValue("");
  expect(screen.getByLabelText("Jahr *")).toHaveValue(null); expect(screen.getByLabelText("Monat *")).toHaveValue("");
  expect(screen.getByRole("button", { name: "SEPA-Vorschau erstellen" })).toBeDisabled();
});
it("uses October, exact amounts and reviewed digest; download never mutates mandate history", async () => {
  show(); await ready(); expect(screen.getByRole("table")).toHaveTextContent(preview.total);
  expect(sepa.previewSepa).toHaveBeenCalledWith({ bank_account_id: "b1", accounting_scope_id: null, year: 2026, month: 10, collection_date: "2026-10-20", message_id: "OCT-2026", pre_notification_on: "2026-10-06", notice_period_days: 14, shorter_period_agreed: false, pre_notification_confirmed: true, bank_profile_confirmed: true });
  fireEvent.click(screen.getByRole("button", { name: "SEPA-XML herunterladen" })); await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  expect(click.mock.instances[0].download).toBe("SEPA_2026_10.xml");
  expect(sepa.downloadSepaXml).toHaveBeenCalledWith(expect.objectContaining({ expected_source_digest: "digest", month: 10 }));
  expect(sepa.updateSepaMandate).not.toHaveBeenCalled(); expect(sepa.createSepaMandate).not.toHaveBeenCalled();
  expect(screen.getByText(/keine Zahlung/)).toBeVisible(); expect(screen.getByText(/niemals an die Bank/)).toBeVisible();
});
it("requires notice timing and a separate shorter-period agreement", async () => {
  show(); await ready(); change("Ankündigungsfrist in Tagen *", "3");
  expect(screen.queryByRole("table")).toBeNull(); expect(screen.getByRole("button", { name: "SEPA-Vorschau erstellen" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/Kürzere Frist/)); expect(screen.getByRole("button", { name: "SEPA-Vorschau erstellen" })).toBeEnabled();
  change("Vorabankündigung erfolgt am *", "2026-10-19"); expect(screen.getByRole("button", { name: "SEPA-Vorschau erstellen" })).toBeDisabled();
});
it("invalidates config and preview on bank or scope selection", async () => {
  show(); await ready(); change("Abrechnungskreis p1 export", "scope"); expect(screen.queryByRole("table")).toBeNull(); expect(screen.queryByLabelText("Gläubiger-ID *")).toBeNull();
  expect(screen.getByRole("button", { name: "SEPA-Konfiguration laden" })).toBeEnabled(); change("SEPA-Bankkonto *", "b2"); expect(screen.queryByText(/SIGNED-1/)).toBeNull();
});
it("read-only can view and request server-authorized exports but never configure", async () => {
  show("READ_ONLY"); await ready(); expect(screen.queryByRole("button", { name: "Gläubiger speichern" })).toBeNull(); expect(screen.queryByRole("button", { name: "Mandat registrieren" })).toBeNull(); expect(screen.queryByRole("button", { name: /widerrufen/ })).toBeNull();
  expect(screen.getByRole("button", { name: "SEPA-XML herunterladen" })).toBeEnabled();
});
it("requires current contact IBAN and retained signed original, then reloads saved config", async () => {
  show(); await load(); change("Mieter für neues Mandat *", "t1"); change("Mandatsreferenz *", "SIGNED-2"); change("Unterschrieben am *", "2026-01-02"); change("Sequenz neues Mandat *", "FRST"); change("Schuldner-Land (ISO) *", "DE"); change("Original-Aufbewahrungsreferenz *", "External 44"); change("IBAN ausdrücklich bestätigen *", "WRONG"); fireEvent.click(screen.getByLabelText(/Das unterschriebene Original/));
  expect(screen.getByRole("button", { name: "Mandat registrieren" })).toBeDisabled(); change("IBAN ausdrücklich bestätigen *", "DE89 3704 0044 0532 0130 00"); fireEvent.click(screen.getByLabelText(/Das unterschriebene Original/));
  fireEvent.click(screen.getByRole("button", { name: "Mandat registrieren" }));
  await waitFor(() => expect(sepa.createSepaMandate).toHaveBeenCalledWith("b1", { tenant_profile_id: "t1", reference: "SIGNED-2", signed_on: "2026-01-02", sequence_type: "FRST", last_used_on: null, debtor_country: "DE", confirmed_iban: "DE89370400440532013000", original_reference: "External 44", mandate_confirmed: true }));
  expect(vi.mocked(sepa.createSepaMandate).mock.calls[0][1]).not.toHaveProperty("debtor_bic");
  await waitFor(() => expect(sepa.getSepaAccount).toHaveBeenCalledTimes(2)); expect(screen.getByLabelText("IBAN ausdrücklich bestätigen *")).toHaveValue("");
});
it("forwards an explicitly entered debtor BIC when registering a non-EEA mandate", async () => {
  const iban = "CH9300762011623852957";
  vi.mocked(sepa.getSepaAccount).mockResolvedValue({ ...account, tenants: [{ ...account.tenants[0], iban }] });
  show(); await load();
  change("Mieter für neues Mandat *", "t1"); change("Mandatsreferenz *", "SIGNED-CH"); change("Unterschrieben am *", "2026-01-02"); change("Sequenz neues Mandat *", "FRST"); change("Schuldner-Land (ISO) *", "CH"); change("IBAN ausdrücklich bestätigen *", iban); change("Original-Aufbewahrungsreferenz *", "External CH");
  expect(screen.getByLabelText("Schuldner-BIC (optional; bei Nicht-EWR erforderlich)")).toHaveValue("");
  change("Schuldner-BIC (optional; bei Nicht-EWR erforderlich)", "POFICHBEXXX");
  fireEvent.click(screen.getByLabelText(/Das unterschriebene Original/));
  expect(screen.getByRole("button", { name: "Mandat registrieren" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "Mandat registrieren" }));
  await waitFor(() => expect(sepa.createSepaMandate).toHaveBeenCalledWith("b1", { tenant_profile_id: "t1", reference: "SIGNED-CH", signed_on: "2026-01-02", sequence_type: "FRST", last_used_on: null, debtor_country: "CH", debtor_bic: "POFICHBEXXX", confirmed_iban: iban, original_reference: "External CH", mandate_confirmed: true }));
});
it("clears 409 preview and allows a fresh review, while 500 is retryable", async () => {
  show(); await ready(); vi.mocked(sepa.downloadSepaXml).mockRejectedValueOnce(new ApiError(500, "Temporär"));
  fireEvent.click(screen.getByRole("button", { name: "SEPA-XML herunterladen" })); expect(await screen.findByRole("alert")).toHaveTextContent("Temporär"); expect(screen.getByRole("table")).toBeVisible();
  vi.mocked(sepa.downloadSepaXml).mockRejectedValueOnce(new ApiError(409, "Quellen geändert")); fireEvent.click(screen.getByRole("button", { name: "SEPA-XML herunterladen" }));
  await waitFor(() => expect(screen.queryByRole("table")).toBeNull()); expect(click).not.toHaveBeenCalled();
});
it("saves creditor edits only after renewed bank confirmation and reloads", async () => {
  show("CLERK"); await ready(); change("Gläubigerort *", "Bonn");
  expect(screen.queryByRole("table")).toBeNull(); expect(screen.getByRole("button", { name: "Gläubiger speichern" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/Gläubigerangaben und bestehende/));
  fireEvent.click(screen.getByRole("button", { name: "Gläubiger speichern" }));
  await waitFor(() => expect(sepa.saveSepaCreditor).toHaveBeenCalledWith("b1", { ...account.creditor, town: "Bonn", bank_authorized: true }));
  await waitFor(() => expect(sepa.getSepaAccount).toHaveBeenCalledTimes(2));
});
it("changes only manually confirmed history and explicitly confirmed revocation", async () => {
  show(); await load(); change("Sequenz SIGNED-1", "RCUR"); change("Tatsächlicher vorheriger Einzug SIGNED-1", "2026-09-20");
  expect(screen.getByRole("button", { name: "Historie SIGNED-1 speichern" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/Historie manuell/)); fireEvent.click(screen.getByRole("button", { name: "Historie SIGNED-1 speichern" }));
  await waitFor(() => expect(sepa.updateSepaMandate).toHaveBeenCalledWith("m1", { sequence_type: "RCUR", last_used_on: "2026-09-20" }));
  await waitFor(() => expect(sepa.getSepaAccount).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("button", { name: "Mandat SIGNED-1 widerrufen" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/Widerruf von SIGNED-1/)); fireEvent.click(screen.getByRole("button", { name: "Mandat SIGNED-1 widerrufen" }));
  await waitFor(() => expect(sepa.revokeSepaMandate).toHaveBeenCalledWith("m1"));
  await waitFor(() => expect(sepa.getSepaAccount).toHaveBeenCalledTimes(3));
  expect(sepa.downloadSepaXml).not.toHaveBeenCalled();
});
it("fails closed with the server's missing-mandate error, not a partial download", async () => {
  vi.mocked(sepa.previewSepa).mockRejectedValue(new ApiError(422, "Mandat für Mieter fehlt: vollständiger Monatsexport blockiert"));
  show(); await load();
  change("Jahr *", "2026"); change("Monat *", "10"); change("Einzugsdatum *", "2026-10-20"); change("Nachrichten-ID *", "MONTH"); change("Vorabankündigung erfolgt am *", "2026-10-06"); change("Ankündigungsfrist in Tagen *", "14");
  fireEvent.click(screen.getByLabelText(/Die tatsächliche Vorabankündigung/)); fireEvent.click(screen.getByLabelText(/Bankprofil pain/));
  fireEvent.click(screen.getByRole("button", { name: "SEPA-Vorschau erstellen" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("vollständiger Monatsexport blockiert"); expect(screen.queryByRole("table")).toBeNull(); expect(sepa.downloadSepaXml).not.toHaveBeenCalled();
});
it.each(["Jahr *", "Monat *", "Einzugsdatum *", "Nachrichten-ID *", "Vorabankündigung erfolgt am *", "Ankündigungsfrist in Tagen *"])("invalidates the reviewed digest when %s changes", async label => {
  show(); await ready(); change(label, label === "Jahr *" ? "2027" : label === "Monat *" ? "11" : label === "Nachrichten-ID *" ? "NEW-ID" : label === "Ankündigungsfrist in Tagen *" ? "13" : "2026-10-21");
  expect(screen.queryByRole("table")).toBeNull(); expect(screen.queryByRole("button", { name: "SEPA-XML herunterladen" })).toBeNull();
});
it("locks duplicate download clicks and permits an explicit retry after failure", async () => {
  show(); await ready(); let reject!: (error: Error) => void;
  vi.mocked(sepa.downloadSepaXml).mockReturnValueOnce(new Promise((_resolve, fail) => { reject = fail; }));
  const button = screen.getByRole("button", { name: "SEPA-XML herunterladen" }); fireEvent.click(button); fireEvent.click(button);
  expect(sepa.downloadSepaXml).toHaveBeenCalledTimes(1); await act(async () => reject(new Error("Netzwerkfehler")));
  expect(await screen.findByRole("alert")).toHaveTextContent("Netzwerkfehler");
  fireEvent.click(screen.getByRole("button", { name: "SEPA-XML herunterladen" })); await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
});
it.each(["change", "unmount"])("never downloads a stale in-flight blob after %s", async mode => {
  const view = show(); await ready(); let resolve!: (blob: Blob) => void; vi.mocked(sepa.downloadSepaXml).mockReturnValue(new Promise(done => { resolve = done; }));
  fireEvent.click(screen.getByRole("button", { name: "SEPA-XML herunterladen" }));
  if (mode === "change") change("Nachrichten-ID *", "OTHER"); else view.unmount();
  await act(async () => resolve(new Blob(["<Document/>"]))); expect(click).not.toHaveBeenCalled();
});
