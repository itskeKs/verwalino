import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as endpoints from "../api/endpoints";
import { ReportsPage } from "../pages/ReportsPage";
import userEvent from "@testing-library/user-event";
import { api, ApiError } from "../api/client";
import * as datev from "../api/datev";
import type { Property } from "../api/types";
import { AuthCtx } from "../auth/useAuth";
import { DatevExport } from "./DatevExport";

const properties: Property[] = ["p1", "p2"].map(id => ({ id, name: id, street: "", zip_code: "", city: "", management_type: "MIET", fiscal_year_start_month: 1, mea_denominator: null, mea_scale_status: "CONFIRMED" }));
const sources: datev.DatevSources = { banks: [{ id: "b1", label: "Objektbank" }], categories: [{ id: "c1", code: "WASSER", label: "Wasser" }], payment_types: ["KALTMIETE"], payment_total: "12345678901234567890.91", cost_total: "-0.09" };
const preview: datev.DatevPreview = { rows: [{ source_type: "payment", source_id: "source1", day: "2026-01-02", amount: "12345678901234567890.91", side: "S", account: "1234", counter_account: "5678", text: "Miete" }], payment_total: sources.payment_total, cost_total: sources.cost_total, source_digest: "digest" };
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
let click: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  vi.spyOn(datev, "getDatevSources").mockResolvedValue(sources);
  vi.spyOn(datev, "previewDatev").mockResolvedValue(preview);
  vi.spyOn(datev, "downloadDatevCsv").mockResolvedValue(new Blob(["\ufeffEXTF"]));
  vi.spyOn(api, "get").mockResolvedValue({ items: [{ id: "s1", property_id: "p1", label: "Exportkreis", scope_type: "MIET", valid_from: "2026-01-01", valid_to: null }], next_cursor: null });
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:datev"), revokeObjectURL: vi.fn() }));
  click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function selection() {
  const user = userEvent.setup();
  await user.selectOptions(screen.getByLabelText("DATEV-Objekt *"), "p1");
  fireEvent.change(screen.getByLabelText("Zeitraum von *"), { target: { value: "2026-01-01" } });
  fireEvent.change(screen.getByLabelText("Zeitraum bis *"), { target: { value: "2026-12-31" } });
  return user;
}
async function configure() {
  const user = await selection();
  await user.click(screen.getByRole("button", { name: "DATEV-Quellen laden" }));
  await screen.findByLabelText("Bankkonto Objektbank *");
  await user.selectOptions(screen.getByLabelText("Kontenrahmen *"), "SKR04");
  fireEvent.change(screen.getByLabelText("Wirtschaftsjahresbeginn *"), { target: { value: "2026-01-01" } });
  for (const [label, value] of [["Beraternummer *", "00123"], ["Mandantennummer *", "00456"], ["Sachkontenlänge *", "4"], ["Bankkonto Objektbank *", "1234"], ["Zahlungstyp KALTMIETE *", "5678"], ["Kategorie Wasser (WASSER) Konto *", "2345"], ["Kategorie Wasser (WASSER) Gegenkonto *", "6789"]]) {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  }
  await user.click(screen.getByRole("checkbox"));
  return user;
}
async function ready() { const user = await configure(); await user.click(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" })); await screen.findByRole("table", { name: "DATEV-Vorschau" }); return user; }

describe("DATEV export", () => {
  it("starts without defaults, requires a whole-property selection and export scope capability", async () => {
    render(<DatevExport properties={properties} />);
    expect(screen.getByLabelText("Kontenrahmen *")).toHaveValue("");
    expect(screen.getByLabelText("Wirtschaftsjahresbeginn *")).toHaveValue("");
    expect(screen.getByLabelText("Sachkontenlänge *")).toHaveValue(null);
    expect(screen.getByRole("button", { name: "DATEV-Quellen laden" })).toBeDisabled();
    await selection();
    expect(datev.getDatevSources).not.toHaveBeenCalled();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(expect.stringContaining("capability=export"), expect.anything()));
    expect(screen.getByText(/vollständigen Exportzugriff/)).toBeVisible();
    expect(screen.getByText(/kein Delta/)).toBeVisible();
    expect(screen.getByText(/kein Importerfolg/)).toBeVisible();
  });
  it("requires every explicit mapping and confirmation; posts exact config without money arithmetic", async () => {
    render(<DatevExport properties={properties} />); const user = await configure();
    await user.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" }));
    expect(await screen.findByRole("table", { name: "DATEV-Vorschau" })).toHaveTextContent("12345678901234567890.91");
    expect(datev.previewDatev).toHaveBeenCalledWith({ property_id: "p1", accounting_scope_id: null, period_start: "2026-01-01", period_end: "2026-12-31", fiscal_year_start: "2026-01-01", chart: "SKR04", advisor_number: "00123", client_number: "00456", account_length: 4, bank_accounts: { b1: "1234" }, payment_accounts: { KALTMIETE: "5678" }, category_accounts: { c1: { account: "2345", counter_account: "6789" } }, gross_accounts_reviewed: true });
  });
  it("clears mappings and review when changing either chart without inserting real accounts", async () => {
    render(<DatevExport properties={properties} />); const user = await ready();
    await user.selectOptions(screen.getByLabelText("Kontenrahmen *"), "SKR03");
    expect(screen.getByLabelText("Bankkonto Objektbank *")).toHaveValue("");
    expect(screen.getByLabelText("Zahlungstyp KALTMIETE *")).toHaveValue("");
    expect(screen.getByLabelText("Kategorie Wasser (WASSER) Konto *")).toHaveValue("");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.queryByRole("table")).toBeNull();
  });
  it.each(["DATEV-Objekt *", "Zeitraum von *", "Zeitraum bis *", "Abrechnungskreis"])("clears sources and mappings on %s change", async label => {
    render(<DatevExport properties={properties} />); const user = await ready();
    if (label === "DATEV-Objekt *" || label === "Abrechnungskreis") await user.selectOptions(screen.getByLabelText(label), label === "Abrechnungskreis" ? "s1" : "p2");
    else fireEvent.change(screen.getByLabelText(label), { target: { value: "2026-02-01" } });
    expect(screen.queryByLabelText("Bankkonto Objektbank *")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "DATEV-Datei herunterladen" })).toBeNull();
  });
  it.each(["Beraternummer *", "Mandantennummer *", "Sachkontenlänge *", "Wirtschaftsjahresbeginn *", "Bankkonto Objektbank *", "Zahlungstyp KALTMIETE *", "Kategorie Wasser (WASSER) Konto *", "Kategorie Wasser (WASSER) Gegenkonto *"])("invalidates preview after %s changes", async label => {
    render(<DatevExport properties={properties} />); await ready();
    fireEvent.change(screen.getByLabelText(label), { target: { value: label.includes("beginn") ? "2025-01-01" : "5" } });
    expect(screen.queryByRole("table")).toBeNull();
  });
  it("rejects reversed dates, invalid account length and nonnumeric/wrong-length accounts", async () => {
    render(<DatevExport properties={properties} />); await configure();
    for (const [label, value] of [["Sachkontenlänge *", "3"], ["Sachkontenlänge *", "9"], ["Sachkontenlänge *", "4.5"], ["Bankkonto Objektbank *", "12x4"], ["Bankkonto Objektbank *", "123"]]) {
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
      expect(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" })).toBeDisabled();
      fireEvent.change(screen.getByLabelText(label), { target: { value: label.startsWith("Sach") ? "4" : "1234" } });
    }
    fireEvent.change(screen.getByLabelText("Zeitraum von *"), { target: { value: "2027-01-01" } });
    expect(screen.getByRole("button", { name: "DATEV-Quellen laden" })).toBeDisabled();
  });
  it("allows READ_ONLY to request server-authorized exports and retries errors", async () => {
    vi.mocked(datev.getDatevSources).mockRejectedValueOnce(new ApiError(403, "Exportrecht fehlt"));
    render(<AuthCtx.Provider value={{ loading: false, session: { user_id: "u", organization_id: "o", role: "READ_ONLY" }, login: vi.fn(), logout: vi.fn() }}><DatevExport properties={properties} /></AuthCtx.Provider>);
    const user = await selection(); await user.click(screen.getByRole("button", { name: "DATEV-Quellen laden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Exportrecht fehlt");
    await user.click(screen.getByRole("button", { name: "DATEV-Quellen laden" }));
    await screen.findByLabelText("Bankkonto Objektbank *");
  });
  it("discards stale preview after 409 and requires a newly reviewed digest before download", async () => {
    vi.mocked(datev.previewDatev).mockResolvedValueOnce(preview).mockResolvedValueOnce({ ...preview, source_digest: "fresh-digest" });
    vi.mocked(datev.downloadDatevCsv).mockRejectedValueOnce(new ApiError(409, "Bestand geändert"));
    render(<DatevExport properties={properties} />); const user = await ready();
    await user.click(screen.getByRole("button", { name: "DATEV-Datei herunterladen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Bestand geändert");
    expect(screen.queryByRole("table", { name: "DATEV-Vorschau" })).toBeNull();
    expect(screen.queryByRole("button", { name: "DATEV-Datei herunterladen" })).toBeNull();
    expect(click).not.toHaveBeenCalled();
    expect(datev.downloadDatevCsv).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" }));
    await screen.findByRole("table", { name: "DATEV-Vorschau" });
    await user.click(screen.getByRole("button", { name: "DATEV-Datei herunterladen" }));
    expect(datev.previewDatev).toHaveBeenCalledTimes(2);
    expect(datev.downloadDatevCsv).toHaveBeenLastCalledWith({ ...vi.mocked(datev.previewDatev).mock.calls[1][0], expected_source_digest: "fresh-digest" });
    expect(click).toHaveBeenCalledTimes(1);
  });
  it("retries temporary preview and download failures without claiming import success; cleans failed browser download", async () => {
    vi.mocked(datev.previewDatev).mockRejectedValueOnce(new Error("Vorschaufehler"));
    vi.mocked(datev.downloadDatevCsv).mockRejectedValueOnce(new ApiError(503, "Vorübergehend nicht verfügbar"));
    render(<DatevExport properties={properties} />); const user = await configure();
    await user.click(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" })); expect(await screen.findByRole("alert")).toHaveTextContent("Vorschaufehler");
    await user.click(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" })); await screen.findByRole("table");
    await user.click(screen.getByRole("button", { name: "DATEV-Datei herunterladen" })); expect(await screen.findByRole("alert")).toHaveTextContent("Vorübergehend nicht verfügbar");
    click.mockImplementationOnce(() => { throw new Error("Browserfehler"); });
    await user.click(screen.getByRole("button", { name: "DATEV-Datei herunterladen" })); expect(await screen.findByRole("alert")).toHaveTextContent("Browserfehler");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:datev"); expect(document.querySelector("a[download]")).toBeNull();
    await user.click(screen.getByRole("button", { name: "DATEV-Datei herunterladen" }));
    expect((click.mock.instances[1] as HTMLAnchorElement).download).toBe("EXTF_Buchungsstapel.csv");
    expect(datev.downloadDatevCsv).toHaveBeenLastCalledWith({ ...vi.mocked(datev.previewDatev).mock.calls[1][0], expected_source_digest: "digest" });
  });
  it.each(["sources", "preview", "download"])("guards duplicate %s and ignores stale replies after selection changes", async kind => {
    const oldSources = deferred<datev.DatevSources>(); const oldPreview = deferred<datev.DatevPreview>(); const oldBlob = deferred<Blob>();
    render(<DatevExport properties={properties} />);
    let button: HTMLElement;
    if (kind === "sources") { await selection(); vi.mocked(datev.getDatevSources).mockReturnValueOnce(oldSources.promise); button = screen.getByRole("button", { name: "DATEV-Quellen laden" }); }
    else if (kind === "preview") { await configure(); vi.mocked(datev.previewDatev).mockReturnValueOnce(oldPreview.promise); button = screen.getByRole("button", { name: "DATEV-Vorschau erstellen" }); }
    else { await ready(); vi.mocked(datev.downloadDatevCsv).mockReturnValueOnce(oldBlob.promise); button = screen.getByRole("button", { name: "DATEV-Datei herunterladen" }); }
    act(() => { button.click(); button.click(); });
    expect(kind === "sources" ? datev.getDatevSources : kind === "preview" ? datev.previewDatev : datev.downloadDatevCsv).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByLabelText("Zeitraum bis *"), { target: { value: "2027-01-01" } });
    await act(async () => { oldSources.resolve(sources); oldPreview.resolve(preview); oldBlob.resolve(new Blob(["old"])); });
    expect(screen.queryByRole("table")).toBeNull(); expect(screen.queryByLabelText("Bankkonto Objektbank *")).toBeNull(); expect(click).not.toHaveBeenCalled();
  });
  it("integrates on ReportsPage using its property list without fetching properties twice", async () => {
    const list = vi.spyOn(endpoints, "listProperties").mockResolvedValue(properties);
    render(<ReportsPage />);
    await screen.findByRole("option", { name: "p1" });
    expect(screen.queryByRole("region", { name: "DATEV-Export" })).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "DATEV-Export öffnen" }));
    const section = screen.getByRole("region", { name: "DATEV-Export" });
    expect(within(section).getByRole("option", { name: "p1" })).toBeInTheDocument();
    expect(list).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "DATEV-Export schließen" }));
    expect(screen.queryByRole("region", { name: "DATEV-Export" })).toBeNull();
  });
  it.each(["sources", "preview", "download"])("ignores older %s errors after a new request succeeds", async kind => {
    const pendingSources = deferred<datev.DatevSources>(); const pendingPreview = deferred<datev.DatevPreview>(); const pendingBlob = deferred<Blob>();
    render(<DatevExport properties={properties} />);
    if (kind === "sources") {
      const user = await selection(); vi.mocked(datev.getDatevSources).mockReturnValueOnce(pendingSources.promise);
      await user.click(screen.getByRole("button", { name: "DATEV-Quellen laden" }));
      fireEvent.change(screen.getByLabelText("Zeitraum bis *"), { target: { value: "2027-01-01" } });
      await user.click(screen.getByRole("button", { name: "DATEV-Quellen laden" }));
      await screen.findByLabelText("Bankkonto Objektbank *");
      await act(async () => pendingSources.reject(new Error("Veralteter Quellenfehler")));
      expect(screen.getByLabelText("Bankkonto Objektbank *")).toBeVisible();
    } else if (kind === "preview") {
      const user = await configure(); vi.mocked(datev.previewDatev).mockReturnValueOnce(pendingPreview.promise);
      await user.click(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" }));
      fireEvent.change(screen.getByLabelText("Beraternummer *"), { target: { value: "999" } });
      await user.click(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" })); await screen.findByRole("table");
      await act(async () => pendingPreview.reject(new Error("Veralteter Vorschaufehler")));
      expect(screen.getByRole("table")).toBeVisible();
    } else {
      const user = await ready(); vi.mocked(datev.downloadDatevCsv).mockReturnValueOnce(pendingBlob.promise);
      await user.click(screen.getByRole("button", { name: "DATEV-Datei herunterladen" }));
      fireEvent.change(screen.getByLabelText("Beraternummer *"), { target: { value: "999" } });
      await user.click(screen.getByRole("button", { name: "DATEV-Vorschau erstellen" })); await screen.findByRole("table");
      await user.click(screen.getByRole("button", { name: "DATEV-Datei herunterladen" }));
      await act(async () => pendingBlob.reject(new Error("Veralteter Downloadfehler")));
      expect(click).toHaveBeenCalledTimes(1);
    }
    expect(screen.queryByRole("alert")).toBeNull();
  });
  it("ignores pending downloads after unmount", async () => {
    const pending = deferred<Blob>(); vi.mocked(datev.downloadDatevCsv).mockReturnValueOnce(pending.promise);
    const view = render(<DatevExport properties={properties} />); const user = await ready();
    await user.click(screen.getByRole("button", { name: "DATEV-Datei herunterladen" })); view.unmount();
    await act(async () => pending.resolve(new Blob(["old"])));
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
