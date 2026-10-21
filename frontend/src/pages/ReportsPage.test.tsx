import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as endpoints from "../api/endpoints";
import { api, ApiError } from "../api/client";
import type { Property, PropertyYearReport } from "../api/types";
import { ReportsPage } from "./ReportsPage";
import { AuthCtx } from "../auth/useAuth";

const property = (id: string, name: string): Property => ({ id, name, street: "", zip_code: "", city: "", management_type: "MIET", fiscal_year_start_month: 1, mea_denominator: null, mea_scale_status: "CONFIRMED" });
const report: PropertyYearReport = {
  property_id: "p1", property_name: "Sonnenhof", year: 2026,
  period_start: "2026-01-01", period_end: "2026-12-31", accounting_scope_id: null,
  income_date_basis: "value_date", expense_date_basis: "invoice_date",
  income: [{ payment_type: "KALTMIETE", amount: "700.00" }, { payment_type: "NK_VORAUSZAHLUNG", amount: "150.00" }, { payment_type: "SONDERZAHLUNG", amount: "-50.00" }],
  expenses: [{ category_id: "secret-category-id", category_code: "WASSER", category_name: "Wasser", tenant_apportionability: "UMLAGEFAEHIG", amount: "100.00" }, { category_id: "other-id", category_code: "REPARATUR", category_name: "Reparatur", tenant_apportionability: "TEILWEISE_PRUEFEN", amount: "25.00" }],
  income_total: "800.00", expense_total: "125.00", balance: "675.00",
  footnote: "Serverhinweis: keine vollständige Steuererklärung und keine steuerliche Einordnung.",
};
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
let click: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  vi.spyOn(endpoints, "listProperties").mockResolvedValue([property("p1", "Sonnenhof"), property("p2", "Parkhof")]);
  vi.spyOn(endpoints, "getPropertyYearReport").mockResolvedValue(report);
  vi.spyOn(endpoints, "getPropertyYearReportCsv").mockResolvedValue(new Blob(["server CSV"], { type: "text/csv" }));
  vi.spyOn(api, "get").mockResolvedValue({ items: [{ id: "scope1", property_id: "p1", label: "Sonnenhof Jahreskreis", scope_type: "MIET", valid_from: "2026-01-01", valid_to: "2026-12-31" }], next_cursor: null });
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:report"), revokeObjectURL: vi.fn() }));
  click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function choose() {
  const user = userEvent.setup();
  await screen.findByRole("option", { name: "Sonnenhof" });
  await user.selectOptions(screen.getByRole("combobox", { name: "Objekt *" }), "p1");
  fireEvent.change(screen.getByLabelText("Jahr *"), { target: { value: "2026" } });
  return user;
}
async function load() { const user = await choose(); await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" })); await screen.findByRole("table", { name: "Einnahmen" }); return user; }

describe("Objekt-Jahresübersicht", () => {
  it("does not fetch reports initially or on selection; renders exact income, expenses, totals, bases and server footnote", async () => {
    render(<ReportsPage />);
    const user = await choose();
    expect(endpoints.getPropertyYearReport).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "CSV herunterladen" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    const income = await screen.findByRole("table", { name: "Einnahmen" });
    expect(within(income).getByText("Kaltmiete")).toBeVisible();
    for (const amount of ["700,00 EUR", "150,00 EUR", "-50,00 EUR"]) expect(within(income).getByText(amount)).toBeVisible();
    const expenses = screen.getByRole("table", { name: "Ausgaben" });
    expect(within(expenses).getByText("Wasser")).toBeVisible();
    expect(within(expenses).getByText("100,00 EUR")).toBeVisible();
    expect(within(expenses).getByText("25,00 EUR")).toBeVisible();
    expect(within(expenses).getByText("Teilweise umlagefaehig")).toBeVisible();
    expect(screen.queryByText("secret-category-id")).toBeNull();
    for (const amount of ["800,00 EUR", "125,00 EUR", "675,00 EUR"]) expect(screen.getByText(amount)).toBeVisible();
    expect(screen.getByText(/Wertstellungsdatum/)).toBeVisible();
    expect(screen.getByText(/Rechnungsdatum/)).toBeVisible();
    expect(screen.getByText(report.footnote)).toBeVisible();
    expect(endpoints.getPropertyYearReport).toHaveBeenCalledWith({ property_id: "p1", year: 2026 });
  });
  it("keeps distinct historical payment types identifiable without inventing classifications", async () => {
    vi.mocked(endpoints.getPropertyYearReport).mockResolvedValue({ ...report,
      income: [{ payment_type: "NACHZAHLUNG", amount: "10.00" },
        { payment_type: "ALT_ERSTATTUNG", amount: "-2.00" }],
    });
    render(<ReportsPage />); await load();
    const income = screen.getByRole("table", { name: "Einnahmen" });
    expect(within(income).getByText("Unbekannter Zahlungstyp (NACHZAHLUNG)")).toBeVisible();
    expect(within(income).getByText("Unbekannter Zahlungstyp (ALT_ERSTATTUNG)")).toBeVisible();
  });
  it("preserves large amounts, signed cents and server totals without client arithmetic", async () => {
    vi.mocked(endpoints.getPropertyYearReport).mockResolvedValue({ ...report, income: [{ payment_type: "KAUTION", amount: "123456789012345678901234.91" }], expenses: [], income_total: "123456789012345678901234.91", expense_total: "0.01", balance: "-0.09" });
    render(<ReportsPage />); await load();
    expect(screen.getAllByText("123.456.789.012.345.678.901.234,91 EUR")).toHaveLength(2);
    expect(screen.getByText("-0,09 EUR")).toBeVisible();
    expect(screen.getByText("0,01 EUR")).toBeVisible();
  });
  it.each(["", "0", "10000", "2026.5", "-1"])("rejects invalid year %s without a request", async year => {
    render(<ReportsPage />); const user = await choose();
    fireEvent.change(screen.getByLabelText("Jahr *"), { target: { value: year } });
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    expect(endpoints.getPropertyYearReport).not.toHaveBeenCalled();
  });
  it.each([1, 9999])("accepts year %s without a current/future rule", async year => {
    render(<ReportsPage />); const user = await choose();
    fireEvent.change(screen.getByLabelText("Jahr *"), { target: { value: String(year) } });
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    await waitFor(() => expect(endpoints.getPropertyYearReport).toHaveBeenCalledWith({ property_id: "p1", year }));
  });
  it("allows READ_ONLY to read and leaves export authorization to the backend", async () => {
    vi.mocked(endpoints.getPropertyYearReportCsv).mockRejectedValueOnce(new ApiError(403, "Exportberechtigung fehlt"));
    render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "reader", organization_id: "org", role: "READ_ONLY" }, login: vi.fn(), logout: vi.fn() }}><ReportsPage /></AuthCtx.Provider>);
    const user = await load();
    expect(screen.queryByRole("button", { name: /speichern|buchen|anlegen/i })).toBeNull();
    await user.click(screen.getByRole("button", { name: "CSV herunterladen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Exportberechtigung fehlt");
    expect(screen.getByRole("table", { name: "Einnahmen" })).toBeVisible(); expect(click).not.toHaveBeenCalled();
  });
  it("requires an object and never exposes raw UUID inputs or write actions", async () => {
    render(<ReportsPage />);
    await screen.findByRole("option", { name: "Sonnenhof" });
    fireEvent.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    expect(endpoints.getPropertyYearReport).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox", { name: /UUID/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /speichern|buchen|anlegen/i })).toBeNull();
  });
  it("uses a property-bound READ scope and sends an explicit circle after 409", async () => {
    vi.mocked(endpoints.getPropertyYearReport).mockRejectedValueOnce(new ApiError(409, "Select an exact accounting scope for this property"));
    render(<ReportsPage />); const user = await choose();
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Select an exact accounting scope");
    expect(api.get).toHaveBeenCalledWith(expect.stringContaining("capability=read"), expect.anything());
    const scope = screen.getByRole("combobox", { name: "Abrechnungskreis" });
    await user.selectOptions(scope, "scope1");
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    await screen.findByRole("table", { name: "Einnahmen" });
    expect(endpoints.getPropertyYearReport).toHaveBeenLastCalledWith({ property_id: "p1", year: 2026, accounting_scope_id: "scope1" });
    expect(vi.mocked(api.get).mock.calls.some(([url]) => url.includes("property_id=p1") && url.includes("capability=read"))).toBe(true);
  });
  it.each([403, 404, 409, 422, 0])("retains inputs after report error %s and permits retry", async status => {
    vi.mocked(endpoints.getPropertyYearReport).mockRejectedValueOnce(status ? new ApiError(status, `Fehler ${status}`) : new Error("Netzwerkfehler"));
    render(<ReportsPage />); const user = await choose(); await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(status ? `Fehler ${status}` : "Netzwerkfehler");
    expect(screen.getByLabelText("Objekt *")).toHaveValue("p1"); expect(screen.getByLabelText("Jahr *")).toHaveValue(2026);
    expect(screen.queryByRole("table")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    expect(await screen.findByRole("table", { name: "Einnahmen" })).toBeVisible();
  });
  it.each(["property", "year", "scope"])("invalidates report and download on %s change until explicit recompute", async kind => {
    render(<ReportsPage />); const user = await load();
    if (kind === "property") await user.selectOptions(screen.getByLabelText("Objekt *"), "p2");
    if (kind === "year") fireEvent.change(screen.getByLabelText("Jahr *"), { target: { value: "2027" } });
    if (kind === "scope") await user.selectOptions(screen.getByLabelText("Abrechnungskreis"), "scope1");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "CSV herunterladen" })).toBeNull();
    expect(endpoints.getPropertyYearReport).toHaveBeenCalledTimes(1);
  });
  it.each(["property", "year", "scope"])("ignores a late report response after %s change", async kind => {
    const pending = deferred<PropertyYearReport>(); vi.mocked(endpoints.getPropertyYearReport).mockReturnValueOnce(pending.promise);
    render(<ReportsPage />); const user = await choose(); await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    if (kind === "property") await user.selectOptions(screen.getByLabelText("Objekt *"), "p2");
    if (kind === "year") fireEvent.change(screen.getByLabelText("Jahr *"), { target: { value: "2027" } });
    if (kind === "scope") await user.selectOptions(screen.getByLabelText("Abrechnungskreis"), "scope1");
    await act(async () => pending.resolve(report));
    expect(screen.queryByRole("table")).toBeNull(); expect(screen.queryByRole("button", { name: "CSV herunterladen" })).toBeNull();
  });
  it("suppresses synchronous duplicate report submits and late responses after unmount", async () => {
    const pending = deferred<PropertyYearReport>(); vi.mocked(endpoints.getPropertyYearReport).mockReturnValueOnce(pending.promise);
    const view = render(<ReportsPage />); await choose();
    const button = screen.getByRole("button", { name: "Übersicht anzeigen" });
    act(() => { button.click(); button.click(); });
    expect(endpoints.getPropertyYearReport).toHaveBeenCalledTimes(1);
    view.unmount(); await act(async () => pending.resolve(report));
    expect(screen.queryByRole("table")).toBeNull();
  });
  it.each(["resolve", "reject"])("ignores an older report %s after a newer report succeeds", async outcome => {
    const old = deferred<PropertyYearReport>(); vi.mocked(endpoints.getPropertyYearReport).mockReturnValueOnce(old.promise).mockResolvedValueOnce({ ...report, year: 2027, balance: "123.45" });
    render(<ReportsPage />); const user = await choose(); await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    fireEvent.change(screen.getByLabelText("Jahr *"), { target: { value: "2027" } });
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" })); await screen.findByText("123,45 EUR");
    await act(async () => { if (outcome === "resolve") old.resolve(report); else old.reject(new Error("Veralteter Fehler")); });
    expect(screen.getByText("123,45 EUR")).toBeVisible(); expect(screen.queryByRole("alert")).toBeNull();
  });
  it("preserves an explicit READ circle through failed report retry and uses it for CSV", async () => {
    vi.mocked(endpoints.getPropertyYearReport).mockRejectedValueOnce(new ApiError(403, "Kreiszugriff verweigert"));
    render(<ReportsPage />); const user = await choose(); await user.selectOptions(screen.getByLabelText("Abrechnungskreis"), "scope1");
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" })); await screen.findByRole("alert");
    expect(screen.getByLabelText("Abrechnungskreis")).toHaveValue("scope1");
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" })); await screen.findByRole("table", { name: "Einnahmen" });
    await user.click(screen.getByRole("button", { name: "CSV herunterladen" }));
    expect(endpoints.getPropertyYearReportCsv).toHaveBeenCalledWith({ property_id: "p1", year: 2026, accounting_scope_id: "scope1" });
  });
  it("clears a selected circle when changing property", async () => {
    render(<ReportsPage />); const user = await choose(); await user.selectOptions(screen.getByLabelText("Abrechnungskreis"), "scope1");
    await user.selectOptions(screen.getByLabelText("Objekt *"), "p2");
    expect(screen.getByLabelText("Abrechnungskreis")).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Übersicht anzeigen" }));
    expect(endpoints.getPropertyYearReport).toHaveBeenCalledWith({ property_id: "p2", year: 2026 });
  });
  it("retries lookup failures without sending any report query", async () => {
    vi.mocked(endpoints.listProperties).mockRejectedValueOnce(new Error("Objekte nicht erreichbar"));
    render(<ReportsPage />); const user = userEvent.setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("Objekte nicht erreichbar");
    await user.click(screen.getByRole("button", { name: "Objekte erneut laden" })); await screen.findByRole("option", { name: "Sonnenhof" });
    expect(screen.queryByRole("alert")).toBeNull(); expect(endpoints.getPropertyYearReport).not.toHaveBeenCalled();
  });
  it("downloads server blob through a real anchor with a revoked object URL and exact report query", async () => {
    render(<ReportsPage />); const user = await load();
    await user.click(screen.getByRole("button", { name: "CSV herunterladen" }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(endpoints.getPropertyYearReportCsv).toHaveBeenCalledWith({ property_id: "p1", year: 2026 });
    expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
    const anchor = click.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("objekt-jahresuebersicht-2026.csv"); expect(anchor.href).toBe("blob:report");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:report");
  });
  it("revokes the blob URL and keeps the report when triggering the browser download fails", async () => {
    click.mockImplementationOnce(() => { throw new Error("Download konnte nicht gestartet werden"); });
    render(<ReportsPage />); const user = await load();
    await user.click(screen.getByRole("button", { name: "CSV herunterladen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Download konnte nicht gestartet werden");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:report");
    expect(document.querySelector("a[download]")).toBeNull();
    expect(screen.getByRole("table", { name: "Einnahmen" })).toBeVisible();
  });
  it.each([403, 404, 409, 0])("keeps the report ready after CSV error %s without a false success", async status => {
    vi.mocked(endpoints.getPropertyYearReportCsv).mockRejectedValueOnce(status ? new ApiError(status, `Exportfehler ${status}`) : new Error("Downloadnetzfehler"));
    render(<ReportsPage />); const user = await load(); await user.click(screen.getByRole("button", { name: "CSV herunterladen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(status ? `Exportfehler ${status}` : "Downloadnetzfehler");
    expect(screen.getByRole("table", { name: "Einnahmen" })).toBeVisible(); expect(click).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "CSV herunterladen" })); await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
  });
  it.each(["property", "year", "scope", "unmount"])("does not start a late download after %s", async kind => {
    const pending = deferred<Blob>(); vi.mocked(endpoints.getPropertyYearReportCsv).mockReturnValueOnce(pending.promise);
    const view = render(<ReportsPage />); const user = await load();
    const button = screen.getByRole("button", { name: "CSV herunterladen" });
    act(() => { button.click(); button.click(); });
    expect(endpoints.getPropertyYearReportCsv).toHaveBeenCalledTimes(1);
    if (kind === "unmount") view.unmount();
    if (kind === "property") await user.selectOptions(screen.getByLabelText("Objekt *"), "p2");
    if (kind === "year") fireEvent.change(screen.getByLabelText("Jahr *"), { target: { value: "2027" } });
    if (kind === "scope") await user.selectOptions(screen.getByLabelText("Abrechnungskreis"), "scope1");
    await act(async () => pending.resolve(new Blob(["CSV"])));
    expect(click).not.toHaveBeenCalled(); expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
